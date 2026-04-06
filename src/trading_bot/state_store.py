from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import requests

from trading_bot.config import BotConfig
from trading_bot.domain import (
    AccountSnapshot,
    BotControlState,
    ManagedPosition,
    OrderPlan,
    Position,
    Signal,
    StrategySetting,
    canonical_symbol,
)
from trading_bot.runtime_state import RuntimeState


DEFAULT_THEME_SETTINGS: dict[str, dict[str, Any]] = {
    "btc_news": {
        "enabled": True,
        "min_surprise": None,
        "min_confidence": None,
        "min_sentiment": None,
        "min_source_count": None,
        "confirmation_bars": None,
        "volume_multiplier": None,
        "max_event_age_seconds": None,
        "risk_per_trade": None,
        "risk_multiplier_min": None,
        "risk_multiplier_max": None,
        "min_trade_score": None,
    },
    "tsla_news": {
        "enabled": True,
        "min_surprise": None,
        "min_confidence": None,
        "min_sentiment": None,
        "min_source_count": None,
        "confirmation_bars": None,
        "volume_multiplier": None,
        "max_event_age_seconds": None,
        "risk_per_trade": None,
        "risk_multiplier_min": None,
        "risk_multiplier_max": None,
        "min_trade_score": None,
    },
    "oil_policy": {
        "enabled": True,
        "min_surprise": None,
        "min_confidence": None,
        "min_sentiment": None,
        "min_source_count": 1,
        "confirmation_bars": None,
        "volume_multiplier": None,
        "max_event_age_seconds": None,
        "risk_per_trade": None,
        "risk_multiplier_min": None,
        "risk_multiplier_max": None,
        "min_trade_score": None,
    },
    "earnings_surprise": {
        "enabled": True,
        "min_surprise": None,
        "min_confidence": None,
        "min_sentiment": None,
        "min_source_count": None,
        "confirmation_bars": None,
        "volume_multiplier": None,
        "max_event_age_seconds": None,
        "risk_per_trade": None,
        "risk_multiplier_min": None,
        "risk_multiplier_max": None,
        "min_trade_score": None,
    },
}


class NullStateStore:
    configured = False

    def __init__(self, config: BotConfig | None = None) -> None:
        self.config = config

    def get_control_state(self) -> BotControlState:
        return BotControlState()

    def set_control_state(
        self,
        *,
        bot_enabled: bool | None = None,
        dry_run_override: bool | None = None,
        emergency_stop_active: bool | None = None,
    ) -> BotControlState:
        return BotControlState(
            bot_enabled=True if bot_enabled is None else bot_enabled,
            dry_run_override=dry_run_override,
            emergency_stop_active=False if emergency_stop_active is None else emergency_stop_active,
            updated_at=datetime.now(timezone.utc),
        )

    def get_strategy_settings(self) -> dict[str, StrategySetting]:
        return _default_strategy_settings()

    def upsert_strategy_setting(self, theme: str, payload: dict[str, Any]) -> StrategySetting:
        merged = {**DEFAULT_THEME_SETTINGS.get(theme, {"enabled": True}), **payload}
        return StrategySetting(theme=theme, updated_at=datetime.now(timezone.utc), **merged)

    def load_runtime_state(self) -> RuntimeState:
        return RuntimeState()

    def save_runtime_state(self, runtime_state: RuntimeState) -> None:
        return None

    def record_news_events(self, events) -> None:
        return None

    def record_signal(self, signal: Signal, *, timestamp: datetime) -> None:
        return None

    def record_order(self, signal: Signal, plan: OrderPlan, *, timestamp: datetime, order_id: str, dry_run: bool) -> None:
        return None

    def record_heartbeat(self, *, status: str, strategy: str, details: dict[str, Any]) -> None:
        return None

    def sync_positions(
        self,
        *,
        account: AccountSnapshot,
        positions: dict[str, Position],
        managed_positions: dict[str, ManagedPosition],
    ) -> None:
        return None

    def list_recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return []

    def list_recent_signals(self, limit: int = 100) -> list[dict[str, Any]]:
        return []

    def list_recent_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        return []

    def list_position_snapshots(self) -> list[dict[str, Any]]:
        return []

    def latest_heartbeat(self) -> dict[str, Any] | None:
        return None


class SupabaseStateStore:
    configured = True

    def __init__(
        self,
        *,
        url: str,
        service_role_key: str,
        anon_key: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key
        self.anon_key = anon_key
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "apikey": self.service_role_key,
                "Authorization": f"Bearer {self.service_role_key}",
                "Content-Type": "application/json",
            }
        )

    def get_control_state(self) -> BotControlState:
        rows = self._select("bot_control", select="*", query={"id": "eq.global"}, limit=1)
        if not rows:
            return self.set_control_state(bot_enabled=True, dry_run_override=None, emergency_stop_active=False)
        row = rows[0]
        return BotControlState(
            bot_enabled=bool(row.get("bot_enabled", True)),
            dry_run_override=row.get("dry_run_override"),
            emergency_stop_active=bool(row.get("emergency_stop_active", False)),
            updated_at=_optional_datetime(row.get("updated_at")),
        )

    def set_control_state(
        self,
        *,
        bot_enabled: bool | None = None,
        dry_run_override: bool | None = None,
        emergency_stop_active: bool | None = None,
    ) -> BotControlState:
        payload = {
            "id": "global",
            "bot_enabled": True if bot_enabled is None else bot_enabled,
            "dry_run_override": dry_run_override,
            "emergency_stop_active": False if emergency_stop_active is None else emergency_stop_active,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        rows = self._upsert("bot_control", [payload], on_conflict="id")
        row = rows[0] if rows else payload
        return BotControlState(
            bot_enabled=bool(row.get("bot_enabled", True)),
            dry_run_override=row.get("dry_run_override"),
            emergency_stop_active=bool(row.get("emergency_stop_active", False)),
            updated_at=_optional_datetime(row.get("updated_at")),
        )

    def get_strategy_settings(self) -> dict[str, StrategySetting]:
        rows = self._select("strategy_settings", select="*", limit=100)
        settings = _default_strategy_settings()
        for row in rows:
            theme = str(row["theme"])
            defaults = DEFAULT_THEME_SETTINGS.get(theme, {"enabled": True})
            settings[theme] = StrategySetting(
                theme=theme,
                enabled=bool(row.get("enabled", defaults.get("enabled", True))),
                min_surprise=_optional_float(row.get("min_surprise")),
                min_confidence=_optional_float(row.get("min_confidence")),
                min_sentiment=_optional_float(row.get("min_sentiment")),
                min_source_count=_optional_int(row.get("min_source_count")),
                confirmation_bars=_optional_int(row.get("confirmation_bars")),
                volume_multiplier=_optional_float(row.get("volume_multiplier")),
                max_event_age_seconds=_optional_int(row.get("max_event_age_seconds")),
                risk_per_trade=_optional_float(row.get("risk_per_trade")),
                risk_multiplier_min=_optional_float(row.get("risk_multiplier_min")),
                risk_multiplier_max=_optional_float(row.get("risk_multiplier_max")),
                min_trade_score=_optional_float(row.get("min_trade_score")),
                updated_at=_optional_datetime(row.get("updated_at")),
            )
        return settings

    def upsert_strategy_setting(self, theme: str, payload: dict[str, Any]) -> StrategySetting:
        record = {
            "theme": theme,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        rows = self._upsert("strategy_settings", [record], on_conflict="theme")
        row = rows[0] if rows else record
        merged = {**DEFAULT_THEME_SETTINGS.get(theme, {"enabled": True}), **row}
        return StrategySetting(
            theme=theme,
            enabled=bool(merged.get("enabled", True)),
            min_surprise=_optional_float(merged.get("min_surprise")),
            min_confidence=_optional_float(merged.get("min_confidence")),
            min_sentiment=_optional_float(merged.get("min_sentiment")),
            min_source_count=_optional_int(merged.get("min_source_count")),
            confirmation_bars=_optional_int(merged.get("confirmation_bars")),
            volume_multiplier=_optional_float(merged.get("volume_multiplier")),
            max_event_age_seconds=_optional_int(merged.get("max_event_age_seconds")),
            risk_per_trade=_optional_float(merged.get("risk_per_trade")),
            risk_multiplier_min=_optional_float(merged.get("risk_multiplier_min")),
            risk_multiplier_max=_optional_float(merged.get("risk_multiplier_max")),
            min_trade_score=_optional_float(merged.get("min_trade_score")),
            updated_at=_optional_datetime(merged.get("updated_at")),
        )

    def load_runtime_state(self) -> RuntimeState:
        state = RuntimeState()

        cooldown_rows = self._select("cooldowns", select="symbol,cooldown_until", limit=1000)
        for row in cooldown_rows:
            cooldown_until = _optional_datetime(row.get("cooldown_until"))
            if cooldown_until is not None:
                state.cooldown_until[str(row["symbol"])] = cooldown_until

        traded_rows = self._select("traded_events", select="event_id", limit=5000)
        state.traded_event_ids = {str(row["event_id"]) for row in traded_rows}

        managed_rows = self._select("managed_positions", select="*", limit=500)
        for row in managed_rows:
            managed_position = _managed_position_from_row(row)
            state.managed_positions[canonical_symbol(managed_position.instrument.symbol)] = managed_position

        daily_rows = self._select("daily_risk_state", select="*", query={"id": "eq.current"}, limit=1)
        if daily_rows:
            row = daily_rows[0]
            state.trading_day = _optional_date(row.get("trading_day"))
            state.daily_start_equity = _optional_float(row.get("daily_start_equity"))
            state.kill_switch_active = bool(row.get("kill_switch_active", False))
        return state

    def save_runtime_state(self, runtime_state: RuntimeState) -> None:
        cooldown_rows = [
            {
                "symbol": symbol,
                "cooldown_until": timestamp.isoformat(),
            }
            for symbol, timestamp in runtime_state.cooldown_until.items()
        ]
        managed_rows = [_managed_position_to_row(position) for position in runtime_state.managed_positions.values()]
        traded_rows = [{"event_id": event_id} for event_id in sorted(runtime_state.traded_event_ids)]

        self._replace_keyed_table("cooldowns", cooldown_rows, key_field="symbol")
        self._replace_keyed_table("managed_positions", managed_rows, key_field="symbol")
        self._replace_keyed_table("traded_events", traded_rows, key_field="event_id")
        self._upsert(
            "daily_risk_state",
            [
                {
                    "id": "current",
                    "trading_day": runtime_state.trading_day.isoformat() if runtime_state.trading_day else None,
                    "daily_start_equity": runtime_state.daily_start_equity,
                    "kill_switch_active": runtime_state.kill_switch_active,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
            on_conflict="id",
        )

    def record_news_events(self, events) -> None:
        payload = []
        now = datetime.now(timezone.utc).isoformat()
        for event in events:
            payload.append(
                {
                    "event_id": event.event_id,
                    "source": event.source,
                    "category": event.category.value,
                    "headline": event.headline,
                    "published_at": event.published_at.isoformat(),
                    "instrument_scope": list(event.instrument_scope),
                    "supporting_sources": list(event.supporting_sources),
                    "source_count": event.source_count,
                    "corroboration_score": event.corroboration_score,
                    "actual_value": event.actual_value,
                    "expected_value": event.expected_value,
                    "surprise_score": event.surprise_score,
                    "sentiment_score": event.sentiment_score,
                    "confidence_score": event.confidence_score,
                    "theme": event.theme,
                    "topic_tags": list(event.topic_tags),
                    "entity_tags": list(event.entity_tags),
                    "direction_score": event.direction_score,
                    "magnitude_score": event.magnitude_score,
                    "unexpectedness_score": event.unexpectedness_score,
                    "trade_score": event.trade_score,
                    "updated_at": now,
                }
            )
        if payload:
            self._upsert("news_events", payload, on_conflict="event_id")

    def record_signal(self, signal: Signal, *, timestamp: datetime) -> None:
        record_id = f"{signal.instrument.symbol}-{signal.event_id or 'noevent'}-{int(timestamp.timestamp())}"
        self._upsert(
            "signal_evaluations",
            [
                {
                    "id": record_id,
                    "timestamp": timestamp.isoformat(),
                    "symbol": signal.instrument.symbol,
                    "action": signal.action.value,
                    "reason": signal.reason,
                    "event_id": signal.event_id,
                    "source": signal.source,
                    "anchor_price": signal.anchor_price,
                    "price": signal.price,
                    "stop_price": signal.stop_price,
                    "actual_value": signal.actual_value,
                    "expected_value": signal.expected_value,
                    "surprise_score": signal.surprise_score,
                    "sentiment_score": signal.sentiment_score,
                    "confidence_score": signal.confidence_score,
                    "source_count": signal.source_count,
                    "corroboration_score": signal.corroboration_score,
                    "supporting_sources": list(signal.supporting_sources),
                    "exit_reason": signal.exit_reason,
                    "risk_multiplier": signal.risk_multiplier,
                    "risk_per_trade_override": signal.risk_per_trade_override,
                    "theme": signal.theme,
                    "topic_tags": list(signal.topic_tags),
                    "entity_tags": list(signal.entity_tags),
                    "direction_score": signal.direction_score,
                    "magnitude_score": signal.magnitude_score,
                    "unexpectedness_score": signal.unexpectedness_score,
                    "trade_score": signal.trade_score,
                }
            ],
            on_conflict="id",
        )

    def record_order(
        self,
        signal: Signal,
        plan: OrderPlan,
        *,
        timestamp: datetime,
        order_id: str,
        dry_run: bool,
    ) -> None:
        self._upsert(
            "orders",
            [
                {
                    "order_id": order_id,
                    "timestamp": timestamp.isoformat(),
                    "symbol": plan.instrument.symbol,
                    "side": plan.side.value,
                    "qty": plan.qty,
                    "notional": plan.notional,
                    "event_id": signal.event_id,
                    "source": signal.source,
                    "reason": plan.signal_reason,
                    "dry_run": dry_run,
                    "exit_reason": signal.exit_reason,
                    "capped_by_buying_power": plan.capped_by_buying_power,
                    "risk_multiplier": plan.risk_multiplier,
                    "risk_per_trade_used": plan.risk_per_trade_used,
                    "theme": signal.theme,
                    "trade_score": signal.trade_score,
                    "anchor_price": signal.anchor_price,
                    "price": signal.price,
                    "status": "submitted",
                }
            ],
            on_conflict="order_id",
        )

    def record_heartbeat(self, *, status: str, strategy: str, details: dict[str, Any]) -> None:
        self._upsert(
            "system_heartbeat",
            [
                {
                    "id": "global",
                    "status": status,
                    "strategy": strategy,
                    "details": details,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
            on_conflict="id",
        )

    def sync_positions(
        self,
        *,
        account: AccountSnapshot,
        positions: dict[str, Position],
        managed_positions: dict[str, ManagedPosition],
    ) -> None:
        rows = []
        for symbol, position in positions.items():
            managed = managed_positions.get(symbol)
            rows.append(
                {
                    "symbol": symbol,
                    "qty": position.qty,
                    "market_value": position.market_value,
                    "avg_entry_price": position.avg_entry_price,
                    "stop_price": managed.stop_price if managed is not None else None,
                    "trailing_stop_price": managed.trailing_stop_price if managed is not None else None,
                    "event_id": managed.event_id if managed is not None else None,
                    "theme": managed.theme if managed is not None else None,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        self._replace_keyed_table("position_snapshots", rows, key_field="symbol")
        self._upsert(
            "system_heartbeat",
            [
                {
                    "id": "account",
                    "status": "account",
                    "strategy": "account",
                    "details": {
                        "equity": account.equity,
                        "cash": account.cash,
                        "buying_power": account.buying_power,
                    },
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
            on_conflict="id",
        )

    def list_recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._select("news_events", select="*", order="published_at.desc", limit=limit)

    def list_recent_signals(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._select("signal_evaluations", select="*", order="timestamp.desc", limit=limit)

    def list_recent_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._select("orders", select="*", order="timestamp.desc", limit=limit)

    def list_position_snapshots(self) -> list[dict[str, Any]]:
        return self._select("position_snapshots", select="*", order="updated_at.desc", limit=100)

    def latest_heartbeat(self) -> dict[str, Any] | None:
        rows = self._select("system_heartbeat", select="*", query={"id": "eq.global"}, limit=1)
        if not rows:
            return None
        return rows[0]

    def _select(
        self,
        table: str,
        *,
        select: str,
        query: dict[str, str] | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {"select": select}
        if query:
            params.update(query)
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = str(limit)
        response = self.session.get(f"{self.url}/rest/v1/{table}", params=params, timeout=20)
        response.raise_for_status()
        return response.json()

    def _upsert(self, table: str, records: list[dict[str, Any]], *, on_conflict: str) -> list[dict[str, Any]]:
        headers = {
            "Prefer": "resolution=merge-duplicates,return=representation",
        }
        response = self.session.post(
            f"{self.url}/rest/v1/{table}",
            params={"on_conflict": on_conflict},
            headers=headers,
            data=json.dumps(records),
            timeout=20,
        )
        response.raise_for_status()
        return response.json() if response.text else []

    def _replace_keyed_table(self, table: str, records: list[dict[str, Any]], *, key_field: str) -> None:
        existing_rows = self._select(table, select=key_field, limit=5000)
        existing_keys = {
            str(row[key_field])
            for row in existing_rows
            if row.get(key_field) not in {None, ""}
        }
        next_keys = {
            str(record[key_field])
            for record in records
            if record.get(key_field) not in {None, ""}
        }
        stale_keys = sorted(existing_keys - next_keys)
        if stale_keys:
            self._delete_in(table, field=key_field, values=stale_keys)
        if records:
            self.session.post(
                f"{self.url}/rest/v1/{table}",
                headers={"Prefer": "resolution=merge-duplicates,return=representation"},
                data=json.dumps(records),
                timeout=20,
            ).raise_for_status()

    def _delete_in(self, table: str, *, field: str, values: list[str]) -> None:
        if not values:
            return
        encoded_values = ",".join(json.dumps(value) for value in values)
        response = self.session.delete(
            f"{self.url}/rest/v1/{table}",
            params={field: f"in.({encoded_values})"},
            timeout=20,
        )
        response.raise_for_status()


def build_state_store(config: BotConfig):
    if config.supabase_url and config.supabase_service_role_key:
        return SupabaseStateStore(
            url=config.supabase_url,
            service_role_key=config.supabase_service_role_key,
            anon_key=config.supabase_anon_key,
        )
    return NullStateStore(config)


def _default_strategy_settings() -> dict[str, StrategySetting]:
    return {
        theme: StrategySetting(theme=theme, **defaults)
        for theme, defaults in DEFAULT_THEME_SETTINGS.items()
    }


def _optional_datetime(raw_value: Any) -> datetime | None:
    if raw_value in {None, ""}:
        return None
    return datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))


def _optional_date(raw_value: Any):
    if raw_value in {None, ""}:
        return None
    return datetime.fromisoformat(f"{raw_value}T00:00:00+00:00").date()


def _optional_float(raw_value: Any) -> float | None:
    if raw_value in {None, ""}:
        return None
    return float(raw_value)


def _optional_int(raw_value: Any) -> int | None:
    if raw_value in {None, ""}:
        return None
    return int(raw_value)


def _managed_position_to_row(position: ManagedPosition) -> dict[str, Any]:
    return {
        "symbol": position.instrument.symbol,
        "asset_class": position.instrument.asset_class.value,
        "qty": position.qty,
        "entry_price": position.entry_price,
        "entry_time": position.entry_time.isoformat(),
        "highest_price": position.highest_price,
        "lowest_price": position.lowest_price,
        "stop_price": position.stop_price,
        "initial_stop_price": position.initial_stop_price,
        "trailing_active": position.trailing_active,
        "trailing_stop_price": position.trailing_stop_price,
        "event_id": position.event_id,
        "source": position.source,
        "anchor_price": position.anchor_price,
        "actual_value": position.actual_value,
        "expected_value": position.expected_value,
        "surprise_score": position.surprise_score,
        "sentiment_score": position.sentiment_score,
        "confidence_score": position.confidence_score,
        "source_count": position.source_count,
        "corroboration_score": position.corroboration_score,
        "supporting_sources": list(position.supporting_sources),
        "target_leverage": position.target_leverage,
        "theme": position.theme,
    }


def _managed_position_from_row(row: dict[str, Any]) -> ManagedPosition:
    from trading_bot.domain import AssetClass, Instrument

    return ManagedPosition(
        instrument=Instrument(
            symbol=str(row["symbol"]),
            asset_class=AssetClass(str(row["asset_class"])),
        ),
        qty=float(row["qty"]),
        entry_price=float(row["entry_price"]),
        entry_time=_optional_datetime(row["entry_time"]) or datetime.now(timezone.utc),
        highest_price=float(row["highest_price"]),
        lowest_price=float(row["lowest_price"]),
        stop_price=float(row["stop_price"]),
        initial_stop_price=float(row["initial_stop_price"]),
        trailing_active=bool(row.get("trailing_active", False)),
        trailing_stop_price=_optional_float(row.get("trailing_stop_price")),
        event_id=row.get("event_id"),
        source=row.get("source"),
        anchor_price=_optional_float(row.get("anchor_price")),
        actual_value=_optional_float(row.get("actual_value")),
        expected_value=_optional_float(row.get("expected_value")),
        surprise_score=_optional_float(row.get("surprise_score")),
        sentiment_score=_optional_float(row.get("sentiment_score")),
        confidence_score=_optional_float(row.get("confidence_score")),
        source_count=_optional_int(row.get("source_count")),
        corroboration_score=_optional_float(row.get("corroboration_score")),
        supporting_sources=tuple(row.get("supporting_sources", []) or ()),
        target_leverage=float(row.get("target_leverage", 1.0)),
        theme=row.get("theme"),
    )
