from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from alpaca.data.timeframe import TimeFrame

from trading_bot.backtest import run_backtest
from trading_bot.domain import (
    AssetClass,
    EarningsRelease,
    Instrument,
    ManagedPosition,
    OrderPlan,
    OrderSide,
    Signal,
    SignalAction,
    StrategyContext,
    StructuredEvent,
    StructuredEventCategory,
    canonical_symbol,
)
from trading_bot.persistence import EarningsDatabase, JsonlTradeLogger
from trading_bot.risk import RiskManager
from trading_bot.runtime_state import RuntimeState
from trading_bot.state_store import NullStateStore
from trading_bot.strategy import TradingStrategy, atr


class EarningsTradingBot:
    def __init__(
        self,
        *,
        provider,
        scanner,
        strategy: TradingStrategy,
        risk_manager: RiskManager,
        config,
        database: EarningsDatabase | None = None,
        trade_logger: JsonlTradeLogger | None = None,
        runtime_state: RuntimeState | None = None,
        telegram_notifier=None,
        state_store=None,
        bot_control_state=None,
    ) -> None:
        self.provider = provider
        self.scanner = scanner
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.config = config
        self.database = database or EarningsDatabase(config.earnings_db_path)
        self.trade_logger = trade_logger or JsonlTradeLogger(config.trade_log_path)
        self.runtime_state = runtime_state or RuntimeState()
        self.telegram_notifier = telegram_notifier
        self.state_store = state_store or NullStateStore(config)
        self.bot_control_state = bot_control_state
        self.current_analyses: dict[str, object] = {}
        self.last_scan_date = None
        self.logger = logging.getLogger("trading_bot.earnings")

    def scan_once(self, *, send_watchlist: bool = True) -> list:
        now = datetime.now(timezone.utc)
        analyses = self.scanner.scan(self.config, as_of=now)
        self.current_analyses = {analysis.candidate.symbol: analysis for analysis in analyses}
        self.last_scan_date = now.date()
        self.logger.info("Bygget earnings-univers med %s kandidater.", len(analyses))
        if (
            send_watchlist
            and analyses
            and self.telegram_notifier is not None
            and self.config.earnings_telegram_watchlist_enabled
        ):
            try:
                self.telegram_notifier.send_earnings_watchlist(
                    analyses=analyses,
                    generated_at=now,
                    limit=self.config.earnings_watchlist_limit,
                )
            except Exception as exc:
                self.logger.warning("Telegram-watchlist feilet: %s", exc)
        return analyses

    def run_once(self) -> None:
        now = datetime.now(timezone.utc)
        if self.last_scan_date != now.date() or not self.current_analyses:
            self.scan_once(send_watchlist=self.config.earnings_telegram_watchlist_enabled)

        account = self.provider.get_account()
        self.runtime_state.reset_for_day_if_needed(now, account.equity)
        self.runtime_state.update_daily_drawdown(
            equity=account.equity,
            max_daily_loss_pct=self.config.earnings_max_daily_loss_pct,
        )

        positions = self.provider.get_all_positions()
        open_symbols = {
            position.symbol.upper()
            for position in positions.values()
            if position.qty != 0
        }
        tracked_symbols = set(self.current_analyses.keys()) | open_symbols
        if not tracked_symbols:
            self.logger.info("Ingen earnings-kandidater å følge akkurat nå.")
            return

        releases_by_symbol = self.scanner.fetch_live_releases(symbols=tracked_symbols, now=now)
        open_positions_count = sum(1 for position in positions.values() if position.qty != 0)
        broker_capabilities = self.provider.get_broker_capabilities()

        for symbol in sorted(tracked_symbols):
            instrument = Instrument(symbol=symbol, asset_class=AssetClass.STOCK)
            bars = self.provider.get_recent_bars(
                instrument=instrument,
                limit=max(self.config.lookback_bars, self.strategy.minimum_bars_required),
            )
            if not bars:
                self.logger.info("Ingen bars tilgjengelig for %s.", symbol)
                continue
            latest_bar = bars[-1]
            current_position = positions.get(canonical_symbol(symbol))
            managed_position = self.runtime_state.get_managed_position(symbol)
            if current_position is not None and managed_position is None:
                managed_position = self._bootstrap_managed_position(instrument, current_position, bars, latest_bar.timestamp)
                if managed_position is not None:
                    self.runtime_state.set_managed_position(managed_position)

            release = self._materialize_release(
                symbol=symbol,
                raw_release=releases_by_symbol.get(symbol),
                latest_bar=latest_bar,
            )
            if release is not None:
                self.state_store.record_news_events((_release_to_structured_event(release),))
            signal = self.strategy.evaluate(
                StrategyContext(
                    instrument=instrument,
                    bars=bars,
                    position_qty=current_position.qty if current_position else 0.0,
                    now=latest_bar.timestamp,
                    managed_position=managed_position,
                    earnings_releases=(release,) if release is not None else (),
                )
            )
            self.runtime_state.apply_signal_updates(symbol, signal)
            self._log_signal(signal, latest_bar.timestamp, release)
            self.logger.info("earnings %s signal=%s reason=%s", symbol, signal.action.value, signal.reason)

            if signal.action is SignalAction.BUY and (
                self.runtime_state.kill_switch_active or not self._entries_enabled()
            ):
                self.logger.warning("Kill switch aktiv. Hopper over nye earnings-trades for %s.", symbol)
                continue
            if signal.action is SignalAction.BUY and not self.runtime_state.can_enter(instrument, latest_bar.timestamp):
                self.logger.info("Cooldown aktiv for earnings-symbol %s.", symbol)
                continue

            plan = self.risk_manager.build_order(
                signal=signal,
                account=account,
                position=current_position,
                open_positions_count=open_positions_count,
                broker_capabilities=broker_capabilities,
            )
            if plan is None:
                continue
            plan = self._apply_session_preferences(plan, signal.price, latest_bar.timestamp)

            if self.config.dry_run:
                order_id = f"dry-run-{symbol}-{int(latest_bar.timestamp.timestamp())}"
                self.logger.info("DRY RUN earnings-ordreplan: %s", plan)
            else:
                order = self.provider.submit_market_order(plan)
                order_id = getattr(order, "id", "ukjent")
                self.logger.info("Sendte earnings-ordre %s for %s", order_id, symbol)

            self._record_order(signal, plan, latest_bar.timestamp, order_id, release)
            self._send_order_notification(signal, plan, latest_bar.timestamp, order_id)

            if signal.action is SignalAction.BUY and plan.side is OrderSide.BUY and plan.qty is not None:
                managed_position = ManagedPosition(
                    instrument=instrument,
                    qty=plan.qty,
                    entry_price=signal.price,
                    entry_time=latest_bar.timestamp,
                    highest_price=signal.highest_price or signal.price,
                    lowest_price=signal.lowest_price or signal.price,
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
                    theme=signal.theme,
                )
                self.runtime_state.record_entry(managed_position)
                if current_position is None:
                    open_positions_count += 1
            elif signal.action is SignalAction.SELL:
                self.runtime_state.record_exit(symbol, latest_bar.timestamp, cooldown_minutes=24 * 60)
                if current_position is not None:
                    open_positions_count = max(0, open_positions_count - 1)

        self.state_store.sync_positions(
            account=account,
            positions=positions,
            managed_positions=self.runtime_state.managed_positions,
        )

    def run_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self.config.loop_interval_seconds)

    def _bootstrap_managed_position(self, instrument, position, bars, now):
        current_atr = atr(bars, 14)
        if current_atr is None:
            return None
        initial_stop = position.avg_entry_price - (2.0 * current_atr)
        return ManagedPosition(
            instrument=instrument,
            qty=position.qty,
            entry_price=position.avg_entry_price,
            entry_time=now,
            highest_price=max(position.avg_entry_price, bars[-1].high),
            lowest_price=min(position.avg_entry_price, bars[-1].low),
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
            source_count=None,
            corroboration_score=None,
            supporting_sources=(),
            target_leverage=1.0,
            theme="earnings_surprise",
        )

    def _entries_enabled(self) -> bool:
        if self.bot_control_state is None:
            return True
        if self.bot_control_state.emergency_stop_active:
            return False
        return self.bot_control_state.bot_enabled

    def _materialize_release(self, *, symbol: str, raw_release: EarningsRelease | None, latest_bar) -> EarningsRelease | None:
        if raw_release is None:
            return None
        analysis = self.current_analyses.get(symbol)
        release = replace(
            raw_release,
            in_universe=analysis is not None,
            extended_hours_eligible=analysis.candidate.extended_hours_eligible if analysis else True,
        )
        stored = self.database.get_release(release.event_id) if self.database is not None else None
        if stored is None:
            release = replace(
                release,
                observed_at=latest_bar.timestamp,
                anchor_price=latest_bar.close,
            )
        else:
            release = replace(
                release,
                observed_at=stored.observed_at,
                anchor_price=stored.anchor_price,
            )
        if self.database is not None:
            self.database.store_release(release)
        return release

    def _apply_session_preferences(self, plan: OrderPlan, price: float, when: datetime) -> OrderPlan:
        if _is_regular_us_session(when):
            return plan
        if plan.side is OrderSide.BUY:
            limit_price = price * 1.002
        else:
            limit_price = price * 0.998
        return replace(
            plan,
            limit_price=limit_price,
            extended_hours=True,
        )

    def _log_signal(self, signal: Signal, timestamp: datetime, release: EarningsRelease | None) -> None:
        self.state_store.record_signal(signal, timestamp=timestamp)
        payload = {
            "timestamp": datetime.now(timezone.utc),
            "instrument": signal.instrument.symbol,
            "action": signal.action.value,
            "reason": signal.reason,
            "event_id": signal.event_id,
            "source": signal.source,
            "actual": signal.actual_value,
            "expected": signal.expected_value,
            "surprise_score": signal.surprise_score,
            "anchor_price": signal.anchor_price,
            "entry_price": signal.price,
            "stop_price": signal.stop_price,
            "trailing_stop_price": signal.trailing_stop_price,
            "exit_reason": signal.exit_reason,
            "risk_multiplier": signal.risk_multiplier,
            "risk_per_trade_override": signal.risk_per_trade_override,
            "theme": signal.theme,
        }
        if release is not None:
            payload.update(
                {
                    "eps_actual": release.eps_actual,
                    "eps_estimate": release.eps_estimate,
                    "revenue_actual": release.revenue_actual,
                    "revenue_estimate": release.revenue_estimate,
                    "eps_surprise_pct": release.eps_surprise_pct,
                    "revenue_surprise_pct": release.revenue_surprise_pct,
                }
            )
        self.trade_logger.log("signal", payload)

    def _record_order(
        self,
        signal: Signal,
        plan: OrderPlan,
        timestamp: datetime,
        order_id: str,
        release: EarningsRelease | None,
    ) -> None:
        payload = {
            "timestamp": timestamp,
            "order_id": order_id,
            "instrument": plan.instrument.symbol,
            "side": plan.side.value,
            "qty": plan.qty,
            "notional": plan.notional,
            "event_id": signal.event_id,
            "source": signal.source,
            "anchor_price": signal.anchor_price,
            "entry_price": signal.price,
            "trailing_stop_price": signal.trailing_stop_price,
            "exit_reason": signal.exit_reason,
            "capped_by_buying_power": plan.capped_by_buying_power,
            "signal_reason": plan.signal_reason,
            "limit_price": plan.limit_price,
            "extended_hours": plan.extended_hours,
            "risk_multiplier": plan.risk_multiplier,
        }
        if release is not None:
            payload.update(
                {
                    "eps_actual": release.eps_actual,
                    "eps_estimate": release.eps_estimate,
                    "revenue_actual": release.revenue_actual,
                    "revenue_estimate": release.revenue_estimate,
                    "eps_surprise_pct": release.eps_surprise_pct,
                    "revenue_surprise_pct": release.revenue_surprise_pct,
                }
            )
        self.trade_logger.log("order", payload)
        self.state_store.record_order(
            signal,
            plan,
            timestamp=timestamp,
            order_id=order_id,
            dry_run=self.config.dry_run,
        )
        if self.database is not None:
            self.database.log_trade(
                timestamp=timestamp,
                event_id=signal.event_id,
                symbol=plan.instrument.symbol,
                action=signal.action.value,
                price=signal.price,
                qty=plan.qty,
                notional=plan.notional,
                order_id=order_id,
                reason=signal.reason,
                dry_run=self.config.dry_run,
                exit_reason=signal.exit_reason,
            )

    def _send_order_notification(self, signal: Signal, plan: OrderPlan, timestamp: datetime, order_id: str) -> None:
        if self.telegram_notifier is None:
            return
        try:
            self.telegram_notifier.send_order_update(
                signal=signal,
                plan=plan,
                timestamp=timestamp,
                order_id=order_id,
                dry_run=self.config.dry_run,
            )
        except Exception as exc:
            self.logger.warning("Telegram-varsling feilet: %s", exc)


def run_earnings_backtest(
    *,
    broker,
    scanner,
    strategy: TradingStrategy,
    risk_manager: RiskManager,
    config,
    from_datetime: datetime,
    to_datetime: datetime,
    initial_cash: float,
) -> dict[str, object]:
    analyses = scanner.scan(config, as_of=from_datetime)
    symbols = {analysis.candidate.symbol for analysis in analyses}
    releases = scanner.fetch_historical_releases(
        symbols=symbols,
        from_date=from_datetime.date(),
        to_date=to_datetime.date(),
    )
    releases_by_symbol: dict[str, list[EarningsRelease]] = {}
    for release in releases:
        releases_by_symbol.setdefault(release.symbol, []).append(release)

    if not releases_by_symbol:
        return {
            "analyses": analyses,
            "results": [],
            "total_trades": 0,
            "ending_equity": initial_cash,
        }

    per_symbol_cash = initial_cash / len(releases_by_symbol)
    results = []
    total_trades = 0
    ending_equity = 0.0
    for symbol, symbol_releases in sorted(releases_by_symbol.items()):
        instrument = Instrument(symbol=symbol, asset_class=AssetClass.STOCK)
        bars = broker.get_historical_bars(
            instrument=instrument,
            start=from_datetime - timedelta(days=1),
            end=to_datetime + timedelta(days=1),
            timeframe=TimeFrame.Minute,
        )
        if not bars:
            continue
        prepared_releases = []
        for release in symbol_releases:
            anchor_price = _anchor_price_for_release(bars, release.observed_at)
            if anchor_price is None:
                continue
            prepared_releases.append(replace(release, anchor_price=anchor_price))
        if not prepared_releases:
            continue
        result = run_backtest(
            instrument=instrument,
            bars=bars,
            strategy=strategy,
            risk_manager=risk_manager,
            initial_cash=per_symbol_cash,
            earnings_releases=tuple(prepared_releases),
        )
        results.append(result)
        total_trades += len(result.trades)
        ending_equity += result.ending_equity

    return {
        "analyses": analyses,
        "results": results,
        "total_trades": total_trades,
        "ending_equity": ending_equity if results else initial_cash,
    }


def _anchor_price_for_release(bars, observed_at: datetime) -> float | None:
    eligible = [bar.close for bar in bars if bar.timestamp <= observed_at]
    if not eligible:
        return None
    return eligible[-1]


def _is_regular_us_session(when: datetime) -> bool:
    eastern = ZoneInfo("America/New_York")
    when_eastern = when.astimezone(eastern)
    if when_eastern.weekday() >= 5:
        return False
    current_minutes = (when_eastern.hour * 60) + when_eastern.minute
    return (9 * 60) + 30 <= current_minutes < (16 * 60)


def _release_to_structured_event(release: EarningsRelease) -> StructuredEvent:
    return StructuredEvent(
        event_id=release.event_id,
        source=release.source,
        instrument_scope=(release.symbol,),
        category=StructuredEventCategory.EARNINGS,
        published_at=release.published_at,
        headline=(
            f"{release.symbol} earnings: EPS {release.eps_actual:.2f} vs {release.eps_estimate:.2f}, "
            f"revenue {release.revenue_actual:.0f} vs {release.revenue_estimate:.0f}"
        ),
        actual_value=release.eps_actual,
        expected_value=release.eps_estimate,
        surprise_score=release.eps_surprise_pct,
        sentiment_score=release.revenue_surprise_pct,
        confidence_score=0.95,
        is_scheduled=True,
        supporting_sources=(release.source,),
        source_count=1,
        corroboration_score=1.0,
        theme="earnings_surprise",
        topic_tags=("earnings",),
        entity_tags=(release.symbol.lower(),),
        direction_score=max(-1.0, min(1.0, release.eps_surprise_pct)),
        magnitude_score=max(0.0, min(1.0, abs(release.eps_surprise_pct))),
        unexpectedness_score=max(0.0, min(1.0, abs(release.revenue_surprise_pct))),
        trade_score=max(0.0, min(1.0, abs(release.eps_surprise_pct) * 0.7 + abs(release.revenue_surprise_pct) * 0.3)),
    )
