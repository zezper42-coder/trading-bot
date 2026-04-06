from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from trading_bot.config import BotConfig
from trading_bot.domain import (
    ManagedPosition,
    OrderSide,
    Signal,
    SignalAction,
    StrategyContext,
    canonical_symbol,
)
from trading_bot.persistence import JsonlTradeLogger
from trading_bot.risk import RiskManager
from trading_bot.runtime_state import RuntimeState
from trading_bot.strategy import TradingStrategy, atr
from trading_bot.surprise_provider import EventJoiner


class TradingBot:
    def __init__(
        self,
        provider,
        strategy: TradingStrategy,
        risk_manager: RiskManager,
        config: BotConfig,
        event_feed=None,
        structured_event_feed=None,
        trade_logger: JsonlTradeLogger | None = None,
        runtime_state: RuntimeState | None = None,
    ) -> None:
        self.provider = provider
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.config = config
        self.event_feed = event_feed
        self.structured_event_feed = structured_event_feed
        self.trade_logger = trade_logger or JsonlTradeLogger(config.trade_log_path)
        self.runtime_state = runtime_state or RuntimeState()
        self.event_joiner = EventJoiner()
        self.logger = logging.getLogger("trading_bot")

    def run_once(self) -> None:
        account = self.provider.get_account()
        now = datetime.now(timezone.utc)
        self.runtime_state.reset_for_day_if_needed(now, account.equity)
        self.runtime_state.update_daily_drawdown(
            equity=account.equity,
            max_daily_loss_pct=self.config.max_daily_loss_pct,
        )

        positions = self.provider.get_all_positions()
        open_positions_count = sum(1 for position in positions.values() if position.qty > 0)
        broker_capabilities = self.provider.get_broker_capabilities()

        for instrument in self.config.instruments:
            bars = self.provider.get_recent_bars(
                instrument=instrument,
                limit=max(self.config.lookback_bars, self.strategy.minimum_bars_required),
            )
            if not bars:
                self.logger.info("Ingen bars tilgjengelig for %s.", instrument.symbol)
                continue

            latest_bar = bars[-1]
            current_position = positions.get(canonical_symbol(instrument.symbol))
            managed_position = self.runtime_state.get_managed_position(instrument.symbol)
            if current_position is not None and managed_position is None:
                managed_position = self._bootstrap_managed_position(instrument, current_position, bars, latest_bar.timestamp)
                if managed_position is not None:
                    self.runtime_state.set_managed_position(managed_position)

            structured_events = self._load_structured_events(instrument, latest_bar.timestamp)
            social_posts = self._load_social_posts(instrument)
            news_events = self._load_news_events(instrument)
            signal = self.strategy.evaluate(
                StrategyContext(
                    instrument=instrument,
                    bars=bars,
                    position_qty=current_position.qty if current_position else 0.0,
                    now=latest_bar.timestamp,
                    managed_position=managed_position,
                    social_posts=social_posts,
                    news_events=news_events,
                    structured_events=structured_events,
                )
            )
            self.runtime_state.apply_signal_updates(instrument.symbol, signal)
            self._log_signal(signal)

            if signal.action is SignalAction.BUY and self.runtime_state.kill_switch_active:
                self.logger.warning("Kill switch aktiv. Hopper over nye trades for %s.", instrument.symbol)
                continue
            if signal.action is SignalAction.BUY and not self.runtime_state.can_enter(instrument, latest_bar.timestamp):
                self.logger.info("Cooldown aktiv for %s.", instrument.symbol)
                continue

            plan = self.risk_manager.build_order(
                signal=signal,
                account=account,
                position=current_position,
                open_positions_count=open_positions_count,
                broker_capabilities=broker_capabilities,
            )
            self.logger.info(
                "%s %s signal=%s reason=%s",
                instrument.asset_class.value,
                instrument.symbol,
                signal.action.value,
                signal.reason,
            )
            if plan is None:
                continue

            if self.config.dry_run:
                self.logger.info("DRY RUN ordreplan: %s", plan)
                order_id = "dry-run"
            else:
                order = self.provider.submit_market_order(plan)
                order_id = getattr(order, "id", "ukjent")
                self.logger.info("Sendte ordre %s for %s", order_id, instrument.symbol)

            self._record_order(signal, plan, latest_bar.timestamp, order_id)
            if plan.side is OrderSide.BUY and plan.qty is not None:
                managed_position = ManagedPosition(
                    instrument=instrument,
                    qty=plan.qty,
                    entry_price=signal.price,
                    entry_time=latest_bar.timestamp,
                    highest_price=signal.highest_price or signal.price,
                    stop_price=signal.stop_price or signal.price,
                    initial_stop_price=signal.stop_price or signal.price,
                    trailing_active=bool(signal.trailing_active),
                    trailing_stop_price=signal.trailing_stop_price,
                    event_id=signal.event_id,
                    source=signal.source,
                    anchor_price=signal.anchor_price,
                    actual_value=signal.actual_value,
                    expected_value=signal.expected_value,
                    surprise_score=signal.surprise_score,
                    sentiment_score=signal.sentiment_score,
                    confidence_score=signal.confidence_score,
                    target_leverage=signal.target_leverage,
                )
                self.runtime_state.record_entry(managed_position)
                if current_position is None:
                    open_positions_count += 1
            elif plan.side is OrderSide.SELL:
                self.runtime_state.record_exit(
                    instrument.symbol,
                    latest_bar.timestamp,
                    cooldown_minutes=60,
                )
                if current_position is not None:
                    open_positions_count = max(0, open_positions_count - 1)

    def run_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self.config.loop_interval_seconds)

    def _bootstrap_managed_position(self, instrument, position, bars, now):
        current_atr = atr(bars, 14)
        if current_atr is None:
            return None
        initial_stop = position.avg_entry_price - (1.5 * current_atr)
        return ManagedPosition(
            instrument=instrument,
            qty=position.qty,
            entry_price=position.avg_entry_price,
            entry_time=now,
            highest_price=max(position.avg_entry_price, bars[-1].high),
            stop_price=initial_stop,
            initial_stop_price=initial_stop,
            trailing_active=False,
            trailing_stop_price=None,
            event_id=None,
            source="bootstrap",
            anchor_price=None,
            actual_value=None,
            expected_value=None,
            surprise_score=None,
            sentiment_score=None,
            confidence_score=None,
            target_leverage=1.0,
        )

    def _load_social_posts(self, instrument):
        if self.event_feed is None:
            return ()
        since = datetime.now(timezone.utc) - timedelta(minutes=self.config.social_lookback_minutes)
        return self.event_feed.get_recent_social_posts(
            instrument=instrument,
            since=since,
        )

    def _load_news_events(self, instrument):
        if self.event_feed is None:
            return ()
        since = datetime.now(timezone.utc) - timedelta(minutes=self.config.news_lookback_minutes)
        return self.event_feed.get_recent_news_events(
            instrument=instrument,
            since=since,
        )

    def _load_structured_events(self, instrument, now):
        if self.structured_event_feed is None:
            return ()
        since = now - timedelta(seconds=self.config.news_shock_max_event_age_seconds)
        recent_headlines = self.provider.get_recent_headlines(
            instrument=instrument,
            since=since,
            limit=5,
        )
        events = self.structured_event_feed.get_recent_structured_events(
            instrument=instrument,
            since=since,
            until=now,
        )
        return self.event_joiner.join(
            instrument=instrument,
            events=events,
            recent_headlines=recent_headlines,
            traded_event_ids=self.runtime_state.traded_event_ids,
        )

    def _log_signal(self, signal: Signal) -> None:
        self.trade_logger.log(
            "signal",
            {
                "timestamp": datetime.now(timezone.utc),
                "instrument": signal.instrument.symbol,
                "action": signal.action.value,
                "reason": signal.reason,
                "event_id": signal.event_id,
                "source": signal.source,
                "actual": signal.actual_value,
                "expected": signal.expected_value,
                "surprise_score": signal.surprise_score,
                "confidence_score": signal.confidence_score,
                "sentiment_score": signal.sentiment_score,
                "anchor_price": signal.anchor_price,
                "entry_price": signal.price,
                "stop_price": signal.stop_price,
                "trailing_stop_price": signal.trailing_stop_price,
                "exit_reason": signal.exit_reason,
            },
        )

    def _record_order(self, signal: Signal, plan, timestamp, order_id: str) -> None:
        self.trade_logger.log(
            "order",
            {
                "timestamp": timestamp,
                "order_id": order_id,
                "instrument": plan.instrument.symbol,
                "side": plan.side.value,
                "qty": plan.qty,
                "notional": plan.notional,
                "event_id": signal.event_id,
                "source": signal.source,
                "actual": signal.actual_value,
                "expected": signal.expected_value,
                "surprise_score": signal.surprise_score,
                "anchor_price": signal.anchor_price,
                "entry_price": signal.price,
                "trailing_stop_price": signal.trailing_stop_price,
                "exit_reason": signal.exit_reason,
                "capped_by_buying_power": plan.capped_by_buying_power,
                "signal_reason": plan.signal_reason,
            },
        )
