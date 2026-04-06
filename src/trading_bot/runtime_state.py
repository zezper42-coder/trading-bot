from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from trading_bot.domain import Instrument, ManagedPosition, Signal, canonical_symbol


@dataclass
class RuntimeState:
    traded_event_ids: set[str] = field(default_factory=set)
    cooldown_until: dict[str, datetime] = field(default_factory=dict)
    managed_positions: dict[str, ManagedPosition] = field(default_factory=dict)
    trading_day: date | None = None
    daily_start_equity: float | None = None
    kill_switch_active: bool = False

    def reset_for_day_if_needed(self, now: datetime, equity: float) -> None:
        today = now.date()
        if self.trading_day == today:
            return
        self.trading_day = today
        self.daily_start_equity = equity
        self.kill_switch_active = False

    def update_daily_drawdown(self, equity: float, max_daily_loss_pct: float) -> None:
        if self.daily_start_equity is None:
            self.daily_start_equity = equity
            return
        threshold = self.daily_start_equity * (1 - max_daily_loss_pct)
        if equity <= threshold:
            self.kill_switch_active = True

    def can_enter(self, instrument: Instrument, now: datetime) -> bool:
        symbol_key = canonical_symbol(instrument.symbol)
        cooldown = self.cooldown_until.get(symbol_key)
        if cooldown is not None and now < cooldown:
            return False
        return True

    def get_managed_position(self, symbol: str) -> ManagedPosition | None:
        return self.managed_positions.get(canonical_symbol(symbol))

    def set_managed_position(self, managed_position: ManagedPosition) -> None:
        self.managed_positions[canonical_symbol(managed_position.instrument.symbol)] = managed_position

    def apply_signal_updates(self, symbol: str, signal: Signal) -> None:
        managed_position = self.get_managed_position(symbol)
        if managed_position is None:
            return
        if signal.highest_price is not None:
            managed_position.highest_price = signal.highest_price
        if signal.lowest_price is not None:
            managed_position.lowest_price = signal.lowest_price
        if signal.stop_price is not None:
            managed_position.stop_price = signal.stop_price
        if signal.trailing_active is not None:
            managed_position.trailing_active = signal.trailing_active
        if signal.trailing_stop_price is not None or signal.trailing_active is False:
            managed_position.trailing_stop_price = signal.trailing_stop_price

    def record_entry(self, managed_position: ManagedPosition) -> None:
        self.set_managed_position(managed_position)
        if managed_position.event_id:
            self.traded_event_ids.add(managed_position.event_id)

    def record_exit(self, symbol: str, now: datetime, cooldown_minutes: int) -> None:
        symbol_key = canonical_symbol(symbol)
        self.managed_positions.pop(symbol_key, None)
        self.cooldown_until[symbol_key] = now + timedelta(minutes=cooldown_minutes)
