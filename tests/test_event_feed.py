import json
from datetime import datetime, timedelta, timezone

from trading_bot.domain import (
    AssetClass,
    HeadlineContext,
    Instrument,
    StructuredEvent,
    StructuredEventCategory,
)
from trading_bot.event_feed import FileStructuredEventFeed, parse_structured_event
from trading_bot.surprise_provider import EventJoiner


def test_parse_structured_event_maps_all_required_fields() -> None:
    event = parse_structured_event(
        {
            "event_id": "evt-1",
            "source": "finnhub",
            "instrument_scope": ["TSLA"],
            "category": "earnings",
            "published_at": "2026-04-04T12:30:00Z",
            "headline": "TSLA beats expectations",
            "actual_value": 1.5,
            "expected_value": 1.2,
            "surprise_score": 0.25,
            "sentiment_score": 0.4,
            "confidence_score": 0.9,
            "is_scheduled": True,
        }
    )

    assert event.event_id == "evt-1"
    assert event.category is StructuredEventCategory.EARNINGS
    assert event.actual_value == 1.5
    assert event.expected_value == 1.2


def test_file_structured_event_feed_filters_by_symbol_and_time(tmp_path) -> None:
    path = tmp_path / "events.json"
    now = datetime.now(timezone.utc)
    payload = [
        {
            "event_id": "evt-1",
            "source": "finnhub",
            "instrument_scope": ["TSLA"],
            "category": "earnings",
            "published_at": now.isoformat(),
            "headline": "TSLA beat",
            "actual_value": 1.5,
            "expected_value": 1.2,
            "surprise_score": 0.25,
            "sentiment_score": 0.4,
            "confidence_score": 0.9,
            "is_scheduled": True,
        },
        {
            "event_id": "evt-2",
            "source": "finnhub",
            "instrument_scope": ["BTC/USD"],
            "category": "macro",
            "published_at": (now - timedelta(days=2)).isoformat(),
            "headline": "Old BTC event",
            "actual_value": 5.0,
            "expected_value": 4.0,
            "surprise_score": 0.25,
            "sentiment_score": 0.4,
            "confidence_score": 0.9,
            "is_scheduled": True,
        },
    ]
    path.write_text(json.dumps(payload))
    feed = FileStructuredEventFeed(path)

    events = feed.get_recent_structured_events(
        instrument=Instrument("TSLA", AssetClass.STOCK),
        since=now - timedelta(hours=1),
        until=now + timedelta(minutes=1),
    )

    assert len(events) == 1
    assert events[0].event_id == "evt-1"


def test_event_joiner_deduplicates_and_attaches_headlines() -> None:
    now = datetime(2026, 4, 4, tzinfo=timezone.utc)
    event = StructuredEvent(
        event_id="evt-1",
        source="finnhub",
        instrument_scope=("TSLA",),
        category=StructuredEventCategory.EARNINGS,
        published_at=now,
        headline="TSLA beat",
        actual_value=1.5,
        expected_value=1.2,
        surprise_score=0.8,
        sentiment_score=0.4,
        confidence_score=0.9,
        is_scheduled=True,
    )
    corroborating_event = StructuredEvent(
        event_id="evt-2",
        source="sec_press",
        instrument_scope=("TSLA",),
        category=StructuredEventCategory.EARNINGS,
        published_at=now + timedelta(minutes=2),
        headline="Tesla beats expectations in update",
        actual_value=1.5,
        expected_value=1.2,
        surprise_score=0.82,
        sentiment_score=0.35,
        confidence_score=0.88,
        is_scheduled=True,
    )
    joiner = EventJoiner()

    joined = joiner.join(
        instrument=Instrument("TSLA", AssetClass.STOCK),
        events=(event, event, corroborating_event),
        recent_headlines=(
            HeadlineContext("Headline A", "alpaca", now),
            HeadlineContext("Headline A", "alpaca", now),
            HeadlineContext("Tesla beats expectations in update", "benzinga", now),
        ),
        traded_event_ids=set(),
    )

    assert len(joined) == 1
    assert len(joined[0].headline_context) == 1
    assert joined[0].source_count >= 3
    assert "finnhub" in joined[0].supporting_sources
    assert "sec_press" in joined[0].supporting_sources
