from __future__ import annotations

from trading_bot.domain import (
    AccountSnapshot,
    BacktestResult,
    BacktestTrade,
    BrokerCapabilities,
    Instrument,
    ManagedPosition,
    OrderSide,
    Position,
    SignalAction,
    StrategyContext,
    StructuredEvent,
)
from trading_bot.risk import RiskManager
from trading_bot.runtime_state import RuntimeState
from trading_bot.strategy import TradingStrategy, atr


def run_backtest(
    instrument: Instrument,
    bars,
    strategy: TradingStrategy,
    risk_manager: RiskManager,
    initial_cash: float,
    events: tuple[StructuredEvent, ...] = (),
) -> BacktestResult:
    cash = initial_cash
    position_qty = 0.0
    managed_position: ManagedPosition | None = None
    trades: list[BacktestTrade] = []
    runtime_state = RuntimeState()
    broker_capabilities = BrokerCapabilities(
        name="backtest",
        max_leverage=4.0,
        supports_crypto_margin=False,
    )

    for index in range(strategy.minimum_bars_required, len(bars) + 1):
        window = bars[:index]
        latest_bar = window[-1]
        runtime_state.reset_for_day_if_needed(latest_bar.timestamp, cash + position_qty * latest_bar.close)
        runtime_state.update_daily_drawdown(
            equity=cash + position_qty * latest_bar.close,
            max_daily_loss_pct=0.015,
        )
        position = None
        if position_qty > 0:
            position = Position(
                symbol=instrument.symbol,
                qty=position_qty,
                market_value=position_qty * latest_bar.close,
                avg_entry_price=managed_position.entry_price if managed_position else latest_bar.close,
            )
        account = AccountSnapshot(
            equity=cash + position_qty * latest_bar.close,
            cash=cash,
            buying_power=cash,
        )
        visible_events = tuple(
            event
            for event in events
            if event.published_at <= latest_bar.timestamp and event.event_id not in runtime_state.traded_event_ids
        )
        signal = strategy.evaluate(
            StrategyContext(
                instrument=instrument,
                bars=window,
                position_qty=position_qty,
                now=latest_bar.timestamp,
                managed_position=managed_position,
                structured_events=visible_events,
            )
        )
        runtime_state.apply_signal_updates(instrument.symbol, signal)
        if signal.action is SignalAction.BUY and runtime_state.kill_switch_active:
            continue
        if signal.action is SignalAction.BUY and not runtime_state.can_enter(instrument, latest_bar.timestamp):
            continue

        plan = risk_manager.build_order(
            signal=signal,
            account=account,
            position=position,
            open_positions_count=1 if position_qty > 0 else 0,
            broker_capabilities=broker_capabilities,
        )
        if plan is None:
            if managed_position is not None and signal.stop_price is not None:
                managed_position.stop_price = signal.stop_price
                managed_position.highest_price = signal.highest_price or managed_position.highest_price
                managed_position.trailing_active = bool(signal.trailing_active)
                managed_position.trailing_stop_price = signal.trailing_stop_price
            continue

        if plan.side is OrderSide.BUY and plan.qty is not None:
            notional = plan.qty * latest_bar.close
            cash -= notional
            position_qty += plan.qty
            managed_position = ManagedPosition(
                instrument=instrument,
                qty=plan.qty,
                entry_price=latest_bar.close,
                entry_time=latest_bar.timestamp,
                highest_price=signal.highest_price or latest_bar.high,
                stop_price=signal.stop_price or latest_bar.close,
                initial_stop_price=signal.stop_price or latest_bar.close,
                trailing_active=False,
                trailing_stop_price=None,
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
            runtime_state.record_entry(managed_position)
            trades.append(
                BacktestTrade(
                    timestamp=latest_bar.timestamp,
                    side=OrderSide.BUY,
                    price=latest_bar.close,
                    qty=plan.qty,
                    notional=notional,
                    event_id=signal.event_id,
                    reason=signal.reason,
                )
            )
            continue

        if plan.side is OrderSide.SELL and plan.qty is not None:
            notional = plan.qty * latest_bar.close
            cash += notional
            trades.append(
                BacktestTrade(
                    timestamp=latest_bar.timestamp,
                    side=OrderSide.SELL,
                    price=latest_bar.close,
                    qty=plan.qty,
                    notional=notional,
                    event_id=signal.event_id,
                    reason=signal.exit_reason or signal.reason,
                )
            )
            position_qty = 0.0
            managed_position = None
            runtime_state.record_exit(instrument.symbol, latest_bar.timestamp, cooldown_minutes=60)

    if position_qty > 0 and managed_position is not None:
        latest_bar = bars[-1]
        cash += position_qty * latest_bar.close
        trades.append(
            BacktestTrade(
                timestamp=latest_bar.timestamp,
                side=OrderSide.SELL,
                price=latest_bar.close,
                qty=position_qty,
                notional=position_qty * latest_bar.close,
                event_id=managed_position.event_id,
                reason="backtest_end_flatten",
            )
        )
        position_qty = 0.0
        managed_position = None

    ending_equity = cash + position_qty * bars[-1].close
    return BacktestResult(
        instrument=instrument,
        trades=trades,
        initial_cash=initial_cash,
        ending_cash=cash,
        ending_position_qty=position_qty,
        ending_equity=ending_equity,
        return_pct=((ending_equity - initial_cash) / initial_cash) * 100,
    )
