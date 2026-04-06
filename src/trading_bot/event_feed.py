from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from trading_bot.domain import (
    HeadlineContext,
    Instrument,
    NewsEvent,
    SocialPost,
    StructuredEvent,
    StructuredEventCategory,
    symbol_in_scope,
)


@dataclass(frozen=True)
class FileEventFeed:
    social_feed_path: Path | None = None
    news_feed_path: Path | None = None

    def get_recent_social_posts(
        self,
        instrument: Instrument,
        since: datetime,
    ) -> tuple[SocialPost, ...]:
        if self.social_feed_path is None or not self.social_feed_path.exists():
            return ()
        raw_posts = _load_json_list(self.social_feed_path)
        posts: list[SocialPost] = []
        for item in raw_posts:
            post = SocialPost(
                id=str(item["id"]),
                source=str(item.get("source", "unknown")),
                author=str(item["author"]).lower(),
                created_at=_parse_datetime(item["created_at"]),
                text=str(item.get("text", "")),
                symbols=_normalize_symbols(item.get("symbols", [])),
                sentiment_score=float(item.get("sentiment_score", 0.0)),
                engagement_score=float(item.get("engagement_score", 0.0)),
            )
            if post.created_at < since:
                continue
            if instrument.symbol not in post.symbols:
                continue
            posts.append(post)
        posts.sort(key=lambda post: post.created_at, reverse=True)
        return tuple(posts)

    def get_recent_news_events(
        self,
        instrument: Instrument,
        since: datetime,
    ) -> tuple[NewsEvent, ...]:
        if self.news_feed_path is None or not self.news_feed_path.exists():
            return ()
        raw_events = _load_json_list(self.news_feed_path)
        events: list[NewsEvent] = []
        for item in raw_events:
            event = NewsEvent(
                id=str(item["id"]),
                source=str(item.get("source", "unknown")),
                headline=str(item.get("headline", "")),
                created_at=_parse_datetime(item["created_at"]),
                symbols=_normalize_symbols(item.get("symbols", [])),
                sentiment_score=float(item.get("sentiment_score", 0.0)),
                surprise_score=float(item.get("surprise_score", 0.0)),
                expected_value=_optional_float(item.get("expected_value")),
                actual_value=_optional_float(item.get("actual_value")),
            )
            if event.created_at < since:
                continue
            if instrument.symbol not in event.symbols:
                continue
            events.append(event)
        events.sort(key=lambda event: event.created_at, reverse=True)
        return tuple(events)


@dataclass(frozen=True)
class FileStructuredEventFeed:
    path: Path

    def get_recent_structured_events(
        self,
        instrument: Instrument,
        since: datetime,
        until: datetime,
    ) -> tuple[StructuredEvent, ...]:
        raw_events = _load_json_list(self.path)
        events: list[StructuredEvent] = []
        for item in raw_events:
            event = parse_structured_event(item)
            if event.published_at < since or event.published_at > until:
                continue
            if not symbol_in_scope(instrument.symbol, event.instrument_scope):
                continue
            events.append(event)
        events.sort(key=lambda event: event.published_at, reverse=True)
        return tuple(events)


def parse_structured_event(item: dict[str, Any]) -> StructuredEvent:
    headline_context = tuple(
        HeadlineContext(
            headline=str(headline_item["headline"]),
            source=str(headline_item.get("source", "unknown")),
            created_at=_parse_datetime(headline_item["created_at"]),
        )
        for headline_item in item.get("headline_context", [])
    )
    return StructuredEvent(
        event_id=str(item["event_id"]),
        source=str(item.get("source", "unknown")),
        instrument_scope=_normalize_symbols(item.get("instrument_scope", [])),
        category=StructuredEventCategory(str(item.get("category", "other")).lower()),
        published_at=_parse_datetime(item["published_at"]),
        headline=str(item.get("headline", "")),
        actual_value=_optional_float(item.get("actual_value")),
        expected_value=_optional_float(item.get("expected_value")),
        surprise_score=float(item.get("surprise_score", 0.0)),
        sentiment_score=float(item.get("sentiment_score", 0.0)),
        confidence_score=float(item.get("confidence_score", 0.0)),
        is_scheduled=bool(item.get("is_scheduled", False)),
        headline_context=headline_context,
        supporting_sources=_normalize_symbols(item.get("supporting_sources", [])),
        source_count=int(item.get("source_count", 1)),
        corroboration_score=float(item.get("corroboration_score", 1.0)),
        theme=str(item.get("theme", "general_news")),
        topic_tags=_normalize_symbols(item.get("topic_tags", [])),
        entity_tags=_normalize_symbols(item.get("entity_tags", [])),
        direction_score=float(item.get("direction_score", 0.0)),
        magnitude_score=float(item.get("magnitude_score", 0.0)),
        unexpectedness_score=float(item.get("unexpectedness_score", 0.0)),
        trade_score=float(item.get("trade_score", 0.0)),
    )


def _load_json_list(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, list):
        raise ValueError(f"Forventet en JSON-liste i {path}.")
    return payload


def _parse_datetime(raw_value: str) -> datetime:
    return datetime.fromisoformat(raw_value.replace("Z", "+00:00"))


def _normalize_symbols(raw_value) -> tuple[str, ...]:
    if not isinstance(raw_value, list):
        return ()
    return tuple(str(symbol) for symbol in raw_value)


def _optional_float(raw_value) -> float | None:
    if raw_value is None or raw_value == "":
        return None
    return float(raw_value)
