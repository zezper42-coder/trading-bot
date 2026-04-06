from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from trading_bot.domain import (
    AccountSnapshot,
    AssetClass,
    Bar,
    BrokerCapabilities,
    HeadlineContext,
    OrderPlan,
    OrderSide,
    Position,
    canonical_symbol,
)


@dataclass(frozen=True)
class SaxoInstrumentRef:
    symbol: str
    uic: int
    asset_type: str
    exchange_id: str | None = None


class SaxoBroker:
    def __init__(
        self,
        access_token: str | None,
        *,
        environment: str,
        account_key: str | None,
        default_exchange_id: str,
        client_key: str | None = None,
        instrument_map: tuple[tuple[str, int], ...] = (),
        session: requests.Session | None = None,
    ) -> None:
        self.access_token = access_token
        self.environment = environment
        self._account_key = account_key
        self.default_exchange_id = default_exchange_id
        self.client_key = client_key
        self.instrument_map = {
            canonical_symbol(symbol): int(uic)
            for symbol, uic in instrument_map
        }
        self.session = session or requests.Session()
        if access_token:
            self.session.headers["Authorization"] = f"Bearer {access_token}"
        self.session.headers.setdefault("Accept", "application/json")
        if client_key:
            self.session.headers.setdefault("X-ClientKey", client_key)
        self._instrument_cache: dict[str, SaxoInstrumentRef] = {}
        self._uic_cache: dict[tuple[int, str], SaxoInstrumentRef] = {}

    @property
    def base_url(self) -> str:
        if self.environment == "live":
            return "https://gateway.saxobank.com/openapi"
        return "https://gateway.saxobank.com/sim/openapi"

    def get_account(self) -> AccountSnapshot:
        self._require_token()
        payload = self._get_json(
            "/port/v1/balances/me",
            params={"AccountKey": self._get_account_key()},
        )
        equity = _coerce_float(_nested_get(payload, "TotalValue", "NetEquityForMargin")) or 0.0
        cash = _coerce_float(
            _nested_get(
                payload,
                "CashBalance",
                "CashAvailableForTrading",
                "AvailableCash",
                ("AvailableFunds",),
                ("BuyingPower", "CashAvailable"),
            )
        )
        buying_power = _coerce_float(
            _nested_get(
                payload,
                "AvailableFunds",
                "CashAvailableForTrading",
                "BuyingPower",
                "CashBalance",
            )
        )
        return AccountSnapshot(
            equity=equity,
            cash=cash if cash is not None else equity,
            buying_power=buying_power if buying_power is not None else (cash if cash is not None else equity),
        )

    def get_broker_capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            name="saxo",
            max_leverage=1.0,
            supports_crypto_margin=False,
            supports_fractional_shares=False,
        )

    def get_all_positions(self) -> dict[str, Position]:
        self._require_token()
        payload = self._get_json(
            "/port/v1/netpositions/me",
            params={"AccountKey": self._get_account_key(), "$top": 500},
        )
        entries = _extract_collection(payload)
        positions: dict[str, Position] = {}
        for entry in entries:
            asset_type = str(
                _nested_get(entry, "AssetType", ("NetPositionBase", "AssetType"), ("PositionBase", "AssetType"))
                or ""
            )
            if asset_type.lower() != "stock":
                continue
            amount = _coerce_float(
                _nested_get(entry, "Amount", ("NetPositionBase", "Amount"), ("PositionBase", "Amount"))
            )
            if amount in {None, 0}:
                continue
            uic = _coerce_int(
                _nested_get(entry, "Uic", ("NetPositionBase", "Uic"), ("PositionBase", "Uic"))
            )
            if uic is None:
                continue
            symbol = self._resolve_symbol_from_uic(uic, asset_type)
            current_price = _coerce_float(
                _nested_get(entry, "CurrentPrice", ("NetPositionView", "CurrentPrice"), ("PositionView", "CurrentPrice"))
            ) or 0.0
            avg_entry_price = _coerce_float(
                _nested_get(entry, "AverageOpenPrice", ("NetPositionBase", "AverageOpenPrice"), ("PositionBase", "AverageOpenPrice"))
            ) or current_price
            positions[canonical_symbol(symbol)] = Position(
                symbol=symbol,
                qty=float(amount),
                market_value=float(amount) * current_price,
                avg_entry_price=avg_entry_price,
            )
        return positions

    def get_recent_bars(self, instrument, limit: int) -> list[Bar]:
        if instrument.asset_class is not AssetClass.STOCK:
            raise RuntimeError("SaxoBroker støtter bare aksjer i dette prosjektet.")
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=max(limit * 5, 14 * 24 * 60))
        bars = self.get_historical_bars(instrument, start=start, end=end, timeframe=TimeFrame.Minute)
        return bars[-limit:]

    def get_historical_bars(self, instrument, start: datetime, end: datetime, timeframe: TimeFrame) -> list[Bar]:
        if instrument.asset_class is not AssetClass.STOCK:
            raise RuntimeError("SaxoBroker støtter bare aksjer i dette prosjektet.")
        instrument_ref = self._resolve_instrument(instrument.symbol)
        horizon_minutes = _timeframe_to_horizon_minutes(timeframe)
        max_samples = 1200
        expected_samples = max(1, math.ceil((end - start).total_seconds() / (horizon_minutes * 60)) + 5)
        remaining = expected_samples
        current_end = end
        seen: set[datetime] = set()
        collected: list[Bar] = []

        while remaining > 0:
            chunk_size = min(max_samples, remaining)
            payload = self._get_json(
                "/chart/v3/charts",
                params={
                    "AccountKey": self._get_account_key(),
                    "AssetType": instrument_ref.asset_type,
                    "Uic": instrument_ref.uic,
                    "Horizon": horizon_minutes,
                    "Count": chunk_size,
                    "Time": current_end.isoformat().replace("+00:00", "Z"),
                    "Mode": "UpTo",
                    "FieldGroups": "Data",
                },
            )
            data = payload.get("Data", []) if isinstance(payload, dict) else []
            if not data:
                break
            batch = []
            for item in data:
                bar = _chart_item_to_bar(item)
                if bar.timestamp in seen:
                    continue
                seen.add(bar.timestamp)
                batch.append(bar)
            if not batch:
                break
            batch.sort(key=lambda bar: bar.timestamp)
            collected = batch + collected
            earliest = batch[0].timestamp
            if earliest <= start:
                break
            current_end = earliest - timedelta(minutes=horizon_minutes)
            remaining -= len(batch)

        filtered = [bar for bar in collected if start <= bar.timestamp <= end]
        filtered.sort(key=lambda bar: bar.timestamp)
        return filtered

    def get_recent_headlines(
        self,
        instrument,
        since: datetime,
        limit: int = 5,
    ) -> tuple[HeadlineContext, ...]:
        return ()

    def submit_market_order(self, plan: OrderPlan):
        self._require_token()
        if plan.instrument.asset_class is not AssetClass.STOCK:
            raise RuntimeError("SaxoBroker støtter bare aksjer i dette prosjektet.")
        if plan.qty is None:
            raise RuntimeError("Saxo market orders krever qty.")
        instrument_ref = self._resolve_instrument(plan.instrument.symbol)
        quantity = int(plan.qty)
        if quantity <= 0:
            raise RuntimeError("Saxo stock orders krever minst 1 hel aksje.")
        payload = {
            "AccountKey": self._get_account_key(),
            "Uic": instrument_ref.uic,
            "AssetType": instrument_ref.asset_type,
            "BuySell": "Buy" if plan.side is OrderSide.BUY else "Sell",
            "Amount": quantity,
            "OrderType": "Market",
            "OrderDuration": {"DurationType": "DayOrder"},
            "ManualOrder": False,
            "ExternalReference": plan.event_id or plan.signal_reason or "trading-bot",
        }
        response = self._request("POST", "/trade/v2/orders", json=payload)
        response.raise_for_status()
        result = response.json() if response.content else {}
        if isinstance(result, dict) and result.get("ErrorInfo"):
            raise RuntimeError(str(result["ErrorInfo"]))
        return result

    def _get_account_key(self) -> str:
        if self._account_key:
            return self._account_key
        payload = self._get_json("/port/v1/accounts/me")
        entries = _extract_collection(payload)
        if not entries and isinstance(payload, dict):
            entries = [payload]
        for entry in entries:
            account_key = entry.get("AccountKey")
            if account_key:
                self._account_key = str(account_key)
                return self._account_key
        raise RuntimeError("Fant ingen Saxo AccountKey. Sett SAXO_ACCOUNT_KEY i .env.")

    def _resolve_instrument(self, symbol: str) -> SaxoInstrumentRef:
        symbol_key = canonical_symbol(symbol)
        cached = self._instrument_cache.get(symbol_key)
        if cached is not None:
            return cached

        if symbol_key in self.instrument_map:
            instrument_ref = SaxoInstrumentRef(
                symbol=symbol,
                uic=self.instrument_map[symbol_key],
                asset_type="Stock",
                exchange_id=self.default_exchange_id,
            )
            self._cache_instrument(instrument_ref)
            return instrument_ref

        lookup_symbol, exchange_id = _split_symbol_and_exchange(symbol, self.default_exchange_id)
        payload = self._get_json(
            "/ref/v1/instruments",
            params={
                "Keywords": lookup_symbol,
                "AssetTypes": "Stock",
                "ExchangeId": exchange_id,
                "$top": 25,
            },
        )
        entries = _extract_collection(payload)
        if not entries:
            raise RuntimeError(f"Fant ingen Saxo-instrumenter for {symbol} på {exchange_id}.")

        picked = _pick_best_instrument(entries, lookup_symbol)
        uic = _coerce_int(_nested_get(picked, "Uic", "Identifier"))
        if uic is None:
            raise RuntimeError(f"Saxo-instrument for {symbol} mangler UIC.")
        instrument_ref = SaxoInstrumentRef(
            symbol=_extract_symbol(picked) or symbol,
            uic=uic,
            asset_type=str(picked.get("AssetType") or "Stock"),
            exchange_id=str(picked.get("ExchangeId") or exchange_id),
        )
        self._cache_instrument(instrument_ref)
        return instrument_ref

    def _resolve_symbol_from_uic(self, uic: int, asset_type: str) -> str:
        cached = self._uic_cache.get((uic, asset_type))
        if cached is not None:
            return cached.symbol
        payload = self._get_json(f"/ref/v1/instruments/details/{uic}/{asset_type}")
        if isinstance(payload, dict) and "Data" in payload and isinstance(payload["Data"], list) and payload["Data"]:
            payload = payload["Data"][0]
        symbol = _extract_symbol(payload) if isinstance(payload, dict) else None
        instrument_ref = SaxoInstrumentRef(
            symbol=symbol or str(uic),
            uic=uic,
            asset_type=asset_type,
            exchange_id=str(payload.get("ExchangeId")) if isinstance(payload, dict) and payload.get("ExchangeId") else None,
        )
        self._cache_instrument(instrument_ref)
        return instrument_ref.symbol

    def _cache_instrument(self, instrument_ref: SaxoInstrumentRef) -> None:
        self._instrument_cache[canonical_symbol(instrument_ref.symbol)] = instrument_ref
        self._uic_cache[(instrument_ref.uic, instrument_ref.asset_type)] = instrument_ref

    def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = self._request("GET", path, params=params)
        response.raise_for_status()
        return response.json()

    def _request(self, method: str, path: str, **kwargs):
        self._require_token()
        return self.session.request(method, f"{self.base_url}{path}", timeout=15, **kwargs)

    def _require_token(self) -> None:
        if self.access_token:
            return
        raise RuntimeError("SAXO_ACCESS_TOKEN mangler.")


def _split_symbol_and_exchange(symbol: str, default_exchange_id: str) -> tuple[str, str]:
    base_symbol, separator, exchange_id = symbol.partition(".")
    if not separator:
        return symbol, default_exchange_id
    return base_symbol, exchange_id.upper()


def _extract_collection(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        data = payload.get("Data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    return []


def _extract_symbol(item: dict[str, Any]) -> str | None:
    for key in ("Symbol", "Identifier", "Ticker", "AssetCode", "Description"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _pick_best_instrument(entries: list[dict[str, Any]], lookup_symbol: str) -> dict[str, Any]:
    lookup_key = canonical_symbol(lookup_symbol)
    for entry in entries:
        symbol = _extract_symbol(entry)
        if symbol and canonical_symbol(symbol) == lookup_key:
            return entry
    return entries[0]


def _timeframe_to_horizon_minutes(timeframe: TimeFrame) -> int:
    unit = timeframe.unit_value
    amount = timeframe.amount_value
    if unit is TimeFrameUnit.Minute:
        return amount
    if unit is TimeFrameUnit.Hour:
        return amount * 60
    if unit is TimeFrameUnit.Day:
        return amount * 1440
    raise RuntimeError(f"Ustøttet timeframe for SaxoBroker: {timeframe}")


def _chart_item_to_bar(item: dict[str, Any]) -> Bar:
    timestamp_raw = item.get("Time")
    if not isinstance(timestamp_raw, str):
        raise RuntimeError("Saxo chart response mangler Time-felt.")
    timestamp = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
    return Bar(
        timestamp=timestamp,
        open=float(item.get("Open", item.get("OpenAsk", item.get("OpenBid", 0.0)))),
        high=float(item.get("High", item.get("HighAsk", item.get("HighBid", 0.0)))),
        low=float(item.get("Low", item.get("LowAsk", item.get("LowBid", 0.0)))),
        close=float(item.get("Close", item.get("CloseAsk", item.get("CloseBid", 0.0)))),
        volume=float(item.get("Volume", 0.0)),
    )


def _nested_get(payload: Any, *candidates) -> Any:
    for candidate in candidates:
        if isinstance(candidate, tuple):
            value = payload
            found = True
            for key in candidate:
                if not isinstance(value, dict) or key not in value:
                    found = False
                    break
                value = value[key]
            if found:
                return value
            continue
        if isinstance(payload, dict) and candidate in payload:
            return payload[candidate]
    return None


def _coerce_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


def _coerce_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    return int(value)
