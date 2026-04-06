from __future__ import annotations

from trading_bot.domain import (
    AccountSnapshot,
    BrokerCapabilities,
    OrderPlan,
    OrderSide,
    Position,
    Signal,
    SignalAction,
)


class RiskManager:
    def __init__(
        self,
        risk_per_trade: float,
        cash_buffer: float,
        max_open_positions: int,
        min_notional_usd: float,
    ) -> None:
        if not 0 < risk_per_trade <= 1:
            raise ValueError("risk_per_trade må være i intervallet (0, 1].")
        if not 0 <= cash_buffer < 1:
            raise ValueError("cash_buffer må være i intervallet [0, 1).")
        if max_open_positions <= 0:
            raise ValueError("max_open_positions må være større enn 0.")
        if min_notional_usd <= 0:
            raise ValueError("min_notional_usd må være større enn 0.")
        self.risk_per_trade = risk_per_trade
        self.cash_buffer = cash_buffer
        self.max_open_positions = max_open_positions
        self.min_notional_usd = min_notional_usd

    def build_order(
        self,
        signal: Signal,
        account: AccountSnapshot,
        position: Position | None,
        open_positions_count: int,
        broker_capabilities: BrokerCapabilities,
    ) -> OrderPlan | None:
        if signal.action is SignalAction.HOLD:
            return None

        if signal.action is SignalAction.SELL:
            if position is None or position.qty <= 0:
                return None
            return OrderPlan(
                instrument=signal.instrument,
                side=OrderSide.SELL,
                qty=position.qty,
                event_id=signal.event_id,
                signal_reason=signal.reason,
            )

        if signal.action is SignalAction.COVER:
            if position is None or position.qty >= 0:
                return None
            return OrderPlan(
                instrument=signal.instrument,
                side=OrderSide.BUY,
                qty=abs(position.qty),
                event_id=signal.event_id,
                signal_reason=signal.reason,
            )

        if position is None and open_positions_count >= self.max_open_positions:
            return None
        if signal.stop_price is None:
            return None

        if signal.action is SignalAction.BUY:
            if signal.stop_price >= signal.price:
                return None
            per_unit_risk = signal.price - signal.stop_price
            order_side = OrderSide.BUY
        elif signal.action is SignalAction.SHORT:
            if signal.instrument.asset_class.value != "stock":
                return None
            if signal.stop_price <= signal.price:
                return None
            per_unit_risk = signal.stop_price - signal.price
            order_side = OrderSide.SELL
        else:
            return None

        base_risk_per_trade = signal.risk_per_trade_override or self.risk_per_trade
        effective_risk_multiplier = max(0.1, signal.risk_multiplier)
        effective_risk_per_trade = min(1.0, base_risk_per_trade * effective_risk_multiplier)
        risk_dollars = account.equity * effective_risk_per_trade
        raw_qty = risk_dollars / per_unit_risk
        desired_notional = raw_qty * signal.price

        reserved_cash = account.equity * self.cash_buffer
        cash_limited_notional = max(0.0, account.cash - reserved_cash)
        allowed_notional = min(account.buying_power, cash_limited_notional)
        if allowed_notional < self.min_notional_usd:
            return None

        capped_by_buying_power = desired_notional > allowed_notional
        final_notional = min(desired_notional, allowed_notional)
        if final_notional < self.min_notional_usd:
            return None

        final_qty = final_notional / signal.price
        if signal.instrument.asset_class.value == "stock" and not broker_capabilities.supports_fractional_shares:
            final_qty = int(final_qty)
            final_notional = final_qty * signal.price
        else:
            final_qty = round(final_qty, 6)
        if final_qty <= 0:
            return None
        if final_notional < self.min_notional_usd:
            return None

        effective_target_leverage = min(signal.target_leverage, broker_capabilities.max_leverage)
        return OrderPlan(
            instrument=signal.instrument,
            side=order_side,
            qty=final_qty,
            notional=round(final_notional, 2),
            capped_by_buying_power=capped_by_buying_power,
            target_leverage=effective_target_leverage,
            risk_multiplier=effective_risk_multiplier,
            risk_per_trade_used=effective_risk_per_trade,
            event_id=signal.event_id,
            signal_reason=signal.reason,
        )
