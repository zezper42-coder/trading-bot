from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Protocol

import requests

from trading_bot.domain import (
    HeadlineContext,
    Instrument,
    StructuredEvent,
    StructuredEventCategory,
    canonical_symbol,
    symbol_in_scope,
    unique_headlines,
)
from trading_bot.event_feed import FileStructuredEventFeed


class StructuredEventFeed(Protocol):
    def get_recent_structured_events(
        self,
        instrument: Instrument,
        since: datetime,
        until: datetime,
    ) -> tuple[StructuredEvent, ...]:
        ...


class CompositeStructuredEventFeed:
    def __init__(self, feeds: tuple[StructuredEventFeed, ...]) -> None:
        self.feeds = feeds

    def get_recent_structured_events(
        self,
        instrument: Instrument,
        since: datetime,
        until: datetime,
    ) -> tuple[StructuredEvent, ...]:
        events: list[StructuredEvent] = []
        for feed in self.feeds:
            events.extend(feed.get_recent_structured_events(instrument, since, until))
        events.sort(key=lambda event: event.published_at, reverse=True)
        return tuple(events)


class FinnhubStructuredEventFeed:
    BASE_URL = "https://finnhub.io/api/v1"
    BTC_MACRO_EVENTS = {
        "consumer price index",
        "cpi",
        "ppi",
        "nonfarm payrolls",
        "nfp",
        "fed interest rate decision",
        "fomc rate decision",
        "interest rate decision",
    }

    def __init__(self, api_key: str, session: requests.Session | None = None) -> None:
        self.api_key = api_key
        self.session = session or requests.Session()

    def get_recent_structured_events(
        self,
        instrument: Instrument,
        since: datetime,
        until: datetime,
    ) -> tuple[StructuredEvent, ...]:
        if canonical_symbol(instrument.symbol) == "TSLA":
            return self._fetch_tsla_earnings(instrument, since, until)
        if canonical_symbol(instrument.symbol) == "BTCUSD":
            return self._fetch_btc_macro(instrument, since, until)
        return ()

    def _fetch_tsla_earnings(
        self,
        instrument: Instrument,
        since: datetime,
        until: datetime,
    ) -> tuple[StructuredEvent, ...]:
        payload = self._get("/stock/earnings", {"symbol": "TSLA"})
        if not isinstance(payload, list):
            return ()
        events: list[StructuredEvent] = []
        for item in payload:
            published_at = _coerce_datetime(item.get("date") or item.get("period"))
            if published_at is None or published_at < since or published_at > until:
                continue
            actual_value = _coerce_float(item.get("actual"))
            expected_value = _coerce_float(item.get("estimate"))
            surprise_score = _coerce_surprise(item)
            if actual_value is None or expected_value is None or surprise_score is None:
                continue
            events.append(
                StructuredEvent(
                    event_id=f"finnhub-earnings-{item.get('symbol','TSLA')}-{published_at.isoformat()}",
                    source="finnhub",
                    instrument_scope=(instrument.symbol,),
                    category=StructuredEventCategory.EARNINGS,
                    published_at=published_at,
                    headline=f"TSLA earnings surprise for {published_at.date().isoformat()}",
                    actual_value=actual_value,
                    expected_value=expected_value,
                    surprise_score=surprise_score,
                    sentiment_score=0.35 if surprise_score > 0 else -0.35,
                    confidence_score=0.9,
                    is_scheduled=True,
                )
            )
        events.sort(key=lambda event: event.published_at, reverse=True)
        return tuple(events)

    def _fetch_btc_macro(
        self,
        instrument: Instrument,
        since: datetime,
        until: datetime,
    ) -> tuple[StructuredEvent, ...]:
        payload = self._get(
            "/calendar/economic",
            {
                "from": since.date().isoformat(),
                "to": until.date().isoformat(),
            },
        )
        items = payload.get("economicCalendar", payload) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return ()
        events: list[StructuredEvent] = []
        for item in items:
            headline = str(item.get("event") or item.get("indicator") or item.get("name") or "").strip()
            if headline.lower() not in self.BTC_MACRO_EVENTS:
                continue
            published_at = _coerce_datetime(item.get("time") or item.get("date"))
            if published_at is None or published_at < since or published_at > until:
                continue
            actual_value = _coerce_float(item.get("actual"))
            expected_value = _coerce_float(
                item.get("estimate") or item.get("consensus") or item.get("forecast")
            )
            if actual_value is None or expected_value is None:
                continue
            surprise_score = _coerce_surprise(item, actual_value, expected_value)
            if surprise_score is None:
                continue
            events.append(
                StructuredEvent(
                    event_id=f"finnhub-macro-{headline.lower()}-{published_at.isoformat()}",
                    source="finnhub",
                    instrument_scope=(instrument.symbol,),
                    category=StructuredEventCategory.MACRO,
                    published_at=published_at,
                    headline=headline,
                    actual_value=actual_value,
                    expected_value=expected_value,
                    surprise_score=surprise_score,
                    sentiment_score=0.25 if surprise_score > 0 else -0.25,
                    confidence_score=0.85,
                    is_scheduled=True,
                )
            )
        events.sort(key=lambda event: event.published_at, reverse=True)
        return tuple(events)

    def _get(self, path: str, params: dict[str, str]) -> object:
        response = self.session.get(
            f"{self.BASE_URL}{path}",
            params={**params, "token": self.api_key},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()


class EventJoiner:
    def join(
        self,
        instrument: Instrument,
        events: tuple[StructuredEvent, ...],
        recent_headlines: tuple[HeadlineContext, ...],
        traded_event_ids: set[str],
    ) -> tuple[StructuredEvent, ...]:
        deduped: dict[str, StructuredEvent] = {}
        for event in events:
            if not symbol_in_scope(instrument.symbol, event.instrument_scope):
                continue
            if event.event_id in traded_event_ids:
                continue
            deduped.setdefault(event.event_id, event)

        unique_recent_headlines = unique_headlines(recent_headlines)
        joined = [
            replace(event, headline_context=unique_recent_headlines)
            for event in deduped.values()
        ]
        joined.sort(key=lambda event: event.published_at, reverse=True)
        return tuple(joined)


def build_structured_event_feed(
    surprise_provider: str,
    finnhub_api_key: str | None,
    structured_events_path,
) -> StructuredEventFeed | None:
    feeds: list[StructuredEventFeed] = []
    if structured_events_path is not None:
        feeds.append(FileStructuredEventFeed(structured_events_path))
    if surprise_provider == "finnhub" and finnhub_api_key:
        feeds.append(FinnhubStructuredEventFeed(finnhub_api_key))
    if not feeds:
        return None
    return CompositeStructuredEventFeed(tuple(feeds))


def _coerce_datetime(raw_value) -> datetime | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        normalized = raw_value.replace("Z", "+00:00")
        if "T" not in normalized:
            normalized = f"{normalized}T00:00:00+00:00"
        return datetime.fromisoformat(normalized)
    return None


def _coerce_float(raw_value) -> float | None:
    if raw_value in {None, ""}:
        return None
    return float(raw_value)


def _coerce_surprise(item: dict, actual_value: float | None = None, expected_value: float | None = None) -> float | None:
    surprise_percent = item.get("surprisePercent") or item.get("surprise")
    if surprise_percent not in {None, ""}:
        value = float(surprise_percent)
        if abs(value) > 1:
            return value / 100
        return value
    if actual_value is None:
        actual_value = _coerce_float(item.get("actual"))
    if expected_value is None:
        expected_value = _coerce_float(item.get("estimate") or item.get("consensus") or item.get("forecast"))
    if actual_value is None or expected_value in {None, 0}:
        return None
    return (actual_value - expected_value) / abs(expected_value)
