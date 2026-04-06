from dataclasses import replace
from datetime import datetime, timezone

from trading_bot.config import load_config
from trading_bot.domain import AssetClass, Instrument, StructuredEvent, StructuredEventCategory
from trading_bot.serverless import InMemoryStructuredEventFeed
from trading_bot.webhook_utils import verify_cron_secret


def test_verify_cron_secret_accepts_matching_bearer_token() -> None:
    assert verify_cron_secret("Bearer abc123", "abc123") is True
    assert verify_cron_secret("Bearer wrong", "abc123") is False
    assert verify_cron_secret(None, "abc123") is False


def test_in_memory_structured_event_feed_filters_by_time_and_symbol() -> None:
    instrument = Instrument("BTC/USD", AssetClass.CRYPTO)
    event = StructuredEvent(
        event_id="evt-1",
        source="test",
        instrument_scope=("BTC/USD",),
        category=StructuredEventCategory.OTHER,
        published_at=datetime(2026, 4, 5, 10, 0, tzinfo=timezone.utc),
        headline="Positive BTC headline",
        actual_value=None,
        expected_value=None,
        surprise_score=0.4,
        sentiment_score=0.2,
        confidence_score=0.9,
        is_scheduled=False,
    )
    feed = InMemoryStructuredEventFeed((event,))

    matching = feed.get_recent_structured_events(
        instrument,
        since=datetime(2026, 4, 5, 9, 0, tzinfo=timezone.utc),
        until=datetime(2026, 4, 5, 11, 0, tzinfo=timezone.utc),
    )
    missing = feed.get_recent_structured_events(
        Instrument("TSLA", AssetClass.STOCK),
        since=datetime(2026, 4, 5, 9, 0, tzinfo=timezone.utc),
        until=datetime(2026, 4, 5, 11, 0, tzinfo=timezone.utc),
    )

    assert matching == (event,)
    assert missing == ()
