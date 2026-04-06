import json
from datetime import datetime, timezone

from trading_bot.domain import StructuredEventCategory
from trading_bot.webhook_bridge import (
    VERCEL_STRUCTURED_EVENT_LOG,
    normalize_finnhub_webhook,
    normalize_x_webhook,
    parse_vercel_log_output,
    structured_event_to_record,
)


def test_normalize_finnhub_webhook_scores_strong_positive_news() -> None:
    payload = {
        "event": "news",
        "data": [
            {
                "id": 42,
                "datetime": "2026-04-04T03:20:00Z",
                "headline": "Tesla wins surprise approval and expands autonomous rollout",
                "related": "TSLA",
                "source": "Reuters",
            }
        ],
    }

    events = normalize_finnhub_webhook(payload, received_at=datetime(2026, 4, 4, 3, 21, tzinfo=timezone.utc))

    assert len(events) == 1
    assert events[0].instrument_scope == ("TSLA",)
    assert events[0].surprise_score >= 0.75
    assert events[0].confidence_score >= 0.8


def test_parse_vercel_log_output_extracts_structured_events() -> None:
    event_payload = normalize_finnhub_webhook(
        {
            "event": "news",
            "data": [
                {
                    "id": 77,
                    "datetime": "2026-04-04T03:20:00Z",
                    "headline": "Bitcoin ETF inflows surge after unexpected approval",
                    "related": "BTC",
                    "source": "Bloomberg",
                }
            ],
        },
        received_at=datetime(2026, 4, 4, 3, 21, tzinfo=timezone.utc),
    )[0]
    raw_output = "\n".join(
        [
            "Retrieving project…",
            json.dumps(
                {
                    "message": json.dumps(
                        {
                            "event": VERCEL_STRUCTURED_EVENT_LOG,
                            "record": structured_event_to_record(event_payload),
                        }
                    )
                }
            ),
        ]
    )

    events = parse_vercel_log_output(raw_output)

    assert len(events) == 1
    assert events[0].category is StructuredEventCategory.OTHER
    assert events[0].instrument_scope == ("BTC/USD",)


def test_normalize_finnhub_webhook_classifies_trump_oil_event() -> None:
    payload = {
        "event": "news",
        "data": [
            {
                "id": 88,
                "datetime": "2026-04-05T01:20:00Z",
                "headline": "Trump signals new sanctions that could tighten global oil supply",
                "source": "Reuters",
            }
        ],
    }

    events = normalize_finnhub_webhook(payload, received_at=datetime(2026, 4, 5, 1, 21, tzinfo=timezone.utc))

    assert len(events) == 1
    assert events[0].theme == "oil_policy"
    assert events[0].category is StructuredEventCategory.ENERGY_POLICY
    assert "USO" in events[0].instrument_scope
    assert events[0].trade_score > 0.65
    assert events[0].direction_score > 0


def test_normalize_x_webhook_marks_event_as_realtime_source() -> None:
    payload = {
        "data": {
            "id": "123",
            "text": "Elon Musk says Tesla robotaxi launch is sooner than expected",
            "created_at": "2026-04-05T18:20:00Z",
            "author_id": "42",
            "public_metrics": {
                "like_count": 5000,
                "retweet_count": 800,
                "reply_count": 100,
                "quote_count": 60,
            },
        },
        "includes": {
            "users": [
                {
                    "id": "42",
                    "username": "elonmusk",
                    "verified": True,
                }
            ]
        },
        "matching_rules": [{"id": "rule-1", "tag": "trading-bot-realtime"}],
    }

    events = normalize_x_webhook(payload, received_at=datetime(2026, 4, 5, 18, 20, 5, tzinfo=timezone.utc))

    assert len(events) == 1
    assert events[0].instrument_scope == ("TSLA",)
    assert events[0].source.startswith("x_webhook:@")
    assert events[0].confidence_score >= 0.8
