from __future__ import annotations

import subprocess
from pathlib import Path
from dataclasses import replace
from datetime import datetime, timedelta
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
from trading_bot.official_feeds import (
    OfficialRssStructuredEventFeed,
    RssFeedDefinition,
    SecCompanySubmissionsFeed,
)
from trading_bot.webhook_bridge import (
    normalize_news_items,
    normalize_x_payload_to_items,
    parse_vercel_log_output,
)


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
            try:
                events.extend(feed.get_recent_structured_events(instrument, since, until))
            except (OSError, SyntaxError, ValueError, subprocess.SubprocessError, requests.RequestException):
                continue
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


class XRecentSearchStructuredEventFeed:
    BASE_URL = "https://api.x.com/2/tweets/search/recent"
    SUPPORTED_SYMBOLS = {"BTCUSD", "TSLA", "USO", "XLE", "OXY", "XOM", "CVX", "SLB"}

    def __init__(
        self,
        bearer_token: str,
        *,
        query: str,
        max_results: int = 25,
        session: requests.Session | None = None,
    ) -> None:
        self.bearer_token = bearer_token
        self.query = query
        self.max_results = max(10, min(max_results, 100))
        self.session = session or requests.Session()
        self.session.headers.setdefault("Authorization", f"Bearer {self.bearer_token}")
        self.session.headers.setdefault("User-Agent", "trading-bot/0.1")
        self._cache_key: tuple[str, str] | None = None
        self._cache_events: tuple[StructuredEvent, ...] = ()

    def get_recent_structured_events(
        self,
        instrument: Instrument,
        since: datetime,
        until: datetime,
    ) -> tuple[StructuredEvent, ...]:
        if canonical_symbol(instrument.symbol) not in self.SUPPORTED_SYMBOLS:
            return ()
        cache_key = (since.isoformat(), until.isoformat())
        if self._cache_key != cache_key:
            response = self.session.get(
                self.BASE_URL,
                params={
                    "query": self.query,
                    "start_time": since.isoformat().replace("+00:00", "Z"),
                    "end_time": until.isoformat().replace("+00:00", "Z"),
                    "max_results": self.max_results,
                    "expansions": "author_id",
                    "tweet.fields": "author_id,created_at,lang,public_metrics",
                    "user.fields": "name,username,verified",
                },
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            items = normalize_x_payload_to_items(payload, source_prefix="x")
            self._cache_events = normalize_news_items(
                items,
                received_at=until,
                envelope_event="x_recent_search",
                event_id_namespace="x-recent-search",
                default_source="x_recent_search",
            )
            self._cache_key = cache_key
        filtered = [
            event
            for event in self._cache_events
            if event.published_at >= since
            and event.published_at <= until
            and symbol_in_scope(instrument.symbol, event.instrument_scope)
        ]
        filtered.sort(key=lambda event: event.published_at, reverse=True)
        return tuple(filtered)


class VercelLogsStructuredEventFeed:
    def __init__(
        self,
        *,
        scope: str | None,
        environment: str,
        since_minutes: int,
        cwd: Path | None = None,
    ) -> None:
        self.scope = scope
        self.environment = environment
        self.since_minutes = since_minutes
        self.cwd = cwd or Path.cwd()

    def get_recent_structured_events(
        self,
        instrument: Instrument,
        since: datetime,
        until: datetime,
    ) -> tuple[StructuredEvent, ...]:
        completed = subprocess.run(
            self._command(),
            cwd=self.cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        events = parse_vercel_log_output(completed.stdout)
        deduped: dict[str, StructuredEvent] = {}
        for event in events:
            if event.published_at < since or event.published_at > until:
                continue
            if not symbol_in_scope(instrument.symbol, event.instrument_scope):
                continue
            deduped.setdefault(event.event_id, event)
        filtered = list(deduped.values())
        filtered.sort(key=lambda event: event.published_at, reverse=True)
        return tuple(filtered)

    def _command(self) -> list[str]:
        command = [
            "vercel",
            "--cwd",
            str(self.cwd),
            "logs",
            "--environment",
            self.environment,
            "--since",
            f"{self.since_minutes}m",
            "--no-follow",
            "--no-branch",
            "--json",
        ]
        if self.scope:
            command.extend(["--scope", self.scope])
        return command


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
        clusters = _cluster_events(tuple(deduped.values()))
        joined = [
            _aggregate_cluster(cluster, unique_recent_headlines)
            for cluster in clusters
        ]
        joined.sort(
            key=lambda event: (
                event.source_count,
                event.corroboration_score,
                event.confidence_score,
                event.published_at,
            ),
            reverse=True,
        )
        return tuple(joined)


def build_structured_event_feed(
    surprise_provider: str,
    finnhub_api_key: str | None,
    structured_events_path,
    vercel_webhook_logs_enabled: bool = False,
    vercel_webhook_scope: str | None = None,
    vercel_webhook_environment: str = "production",
    vercel_webhook_logs_since_minutes: int = 15,
    vercel_webhook_cwd: Path | None = None,
    official_rss_feeds_enabled: bool = False,
    official_rss_feeds: tuple[tuple[str, str], ...] = (),
    sec_tsla_submissions_enabled: bool = False,
    sec_api_user_agent: str = "trading-bot/0.1",
    x_recent_search_enabled: bool = False,
    x_bearer_token: str | None = None,
    x_recent_search_query: str = "",
    x_recent_search_max_results: int = 25,
) -> StructuredEventFeed | None:
    feeds: list[StructuredEventFeed] = []
    if vercel_webhook_logs_enabled:
        feeds.append(
            VercelLogsStructuredEventFeed(
                scope=vercel_webhook_scope,
                environment=vercel_webhook_environment,
                since_minutes=vercel_webhook_logs_since_minutes,
                cwd=vercel_webhook_cwd,
            )
        )
    if official_rss_feeds_enabled and official_rss_feeds:
        feeds.append(
            OfficialRssStructuredEventFeed(
                tuple(
                    RssFeedDefinition(name=name, url=url)
                    for name, url in official_rss_feeds
                ),
                user_agent=sec_api_user_agent,
            )
        )
    if sec_tsla_submissions_enabled:
        feeds.append(
            SecCompanySubmissionsFeed(
                {"TSLA": "0001318605"},
                user_agent=sec_api_user_agent,
            )
        )
    if x_recent_search_enabled and x_bearer_token and x_recent_search_query:
        feeds.append(
            XRecentSearchStructuredEventFeed(
                x_bearer_token,
                query=x_recent_search_query,
                max_results=x_recent_search_max_results,
            )
        )
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


def _normalize_x_recent_search_payload(payload: object) -> tuple[dict[str, object], ...]:
    return normalize_x_payload_to_items(payload, source_prefix="x")


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


def _cluster_events(events: tuple[StructuredEvent, ...]) -> list[list[StructuredEvent]]:
    ordered = sorted(
        events,
        key=lambda event: (
            event.published_at,
            event.confidence_score,
            event.surprise_score,
        ),
        reverse=True,
    )
    clusters: list[list[StructuredEvent]] = []
    for event in ordered:
        cluster = next((cluster for cluster in clusters if _events_match(cluster[0], event)), None)
        if cluster is None:
            clusters.append([event])
        else:
            cluster.append(event)
    return clusters


def _events_match(left: StructuredEvent, right: StructuredEvent) -> bool:
    if not set(canonical_symbol(symbol) for symbol in left.instrument_scope).intersection(
        canonical_symbol(symbol) for symbol in right.instrument_scope
    ):
        return False
    if abs((left.published_at - right.published_at).total_seconds()) > 30 * 60:
        return False
    if (
        left.category == right.category
        and left.is_scheduled
        and right.is_scheduled
        and abs((left.published_at - right.published_at).total_seconds()) <= 15 * 60
    ):
        return True
    return _headline_overlap_ratio(left.headline, right.headline) >= 0.25


def _aggregate_cluster(
    cluster: list[StructuredEvent],
    recent_headlines: tuple[HeadlineContext, ...],
) -> StructuredEvent:
    primary = max(
        cluster,
        key=lambda event: (
            event.confidence_score,
            event.surprise_score,
            event.actual_value is not None,
            event.published_at,
        ),
    )
    matching_headlines = tuple(
        headline
        for headline in recent_headlines
        if _headline_overlap_ratio(primary.headline, headline.headline) >= 0.2
    )
    supporting_sources = {
        source
        for event in cluster
        for source in ((event.source,) + event.supporting_sources)
        if source
    }
    supporting_sources.update(f"alpaca:{headline.source}" for headline in matching_headlines)
    source_count = len(supporting_sources)
    corroboration_score = round(
        sum(max(event.confidence_score, 0.05) for event in cluster)
        + (0.2 * len(matching_headlines)),
        3,
    )
    return replace(
        primary,
        headline_context=unique_headlines(primary.headline_context + matching_headlines),
        supporting_sources=tuple(sorted(supporting_sources)),
        source_count=source_count,
        corroboration_score=corroboration_score,
    )


STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "after",
    "this",
    "that",
    "tesla",
    "bitcoin",
    "btc",
    "tsla",
    "news",
    "filing",
    "sec",
}


def _headline_overlap_ratio(left: str, right: str) -> float:
    left_tokens = _headline_tokens(left)
    right_tokens = _headline_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = left_tokens.intersection(right_tokens)
    return len(overlap) / max(min(len(left_tokens), len(right_tokens)), 1)


def _headline_tokens(text: str) -> set[str]:
    cleaned = []
    for raw_token in text.lower().replace("/", " ").replace("-", " ").split():
        token = "".join(character for character in raw_token if character.isalnum())
        token = _normalize_token(token)
        if len(token) < 3 or token in STOP_WORDS:
            continue
        cleaned.append(token)
    return set(cleaned)


def _normalize_token(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token
