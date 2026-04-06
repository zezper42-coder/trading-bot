from datetime import datetime, timezone

from trading_bot.domain import AssetClass, Instrument
from trading_bot.surprise_provider import (
    XRecentSearchStructuredEventFeed,
    _normalize_x_recent_search_payload,
)


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _FakeSession:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.headers: dict[str, str] = {}

    def get(self, *_args, **_kwargs) -> _FakeResponse:
        return _FakeResponse(self.payload)


def test_normalize_x_recent_search_payload_builds_news_items() -> None:
    payload = {
        "data": [
            {
                "id": "post-1",
                "text": "Strategy just approved a bigger bitcoin treasury and adds more BTC",
                "created_at": "2026-04-05T16:20:00Z",
                "author_id": "u-1",
                "public_metrics": {
                    "like_count": 350,
                    "retweet_count": 90,
                    "reply_count": 22,
                    "quote_count": 14,
                },
            }
        ],
        "includes": {
            "users": [
                {
                    "id": "u-1",
                    "username": "btcarchive",
                    "verified": True,
                }
            ]
        },
    }

    items = _normalize_x_recent_search_payload(payload)

    assert len(items) == 1
    assert items[0]["source"] == "x:@btcarchive"
    assert items[0]["related"] == "BTC"
    assert items[0]["confidence_score"] > 0.8


def test_x_recent_search_feed_returns_btc_structured_event() -> None:
    payload = {
        "data": [
            {
                "id": "post-2",
                "text": "Unexpected approval sends spot bitcoin ETF inflows sharply higher",
                "created_at": "2026-04-05T16:22:00Z",
                "author_id": "u-2",
                "public_metrics": {
                    "like_count": 110,
                    "retweet_count": 40,
                    "reply_count": 8,
                    "quote_count": 3,
                },
            }
        ],
        "includes": {
            "users": [
                {
                    "id": "u-2",
                    "username": "tier10k",
                    "verified": True,
                }
            ]
        },
    }
    feed = XRecentSearchStructuredEventFeed(
        "test-token",
        query="bitcoin approval",
        session=_FakeSession(payload),
    )

    events = feed.get_recent_structured_events(
        Instrument("BTC/USD", AssetClass.CRYPTO),
        since=datetime(2026, 4, 5, 16, 0, tzinfo=timezone.utc),
        until=datetime(2026, 4, 5, 16, 30, tzinfo=timezone.utc),
    )

    assert len(events) == 1
    assert events[0].theme == "btc_news"
    assert events[0].source == "x:@tier10k"
    assert events[0].instrument_scope == ("BTC/USD",)
    assert events[0].trade_score > 0


def test_x_recent_search_feed_supports_tsla_scope() -> None:
    payload = {
        "data": [
            {
                "id": "post-3",
                "text": "Tesla robotaxi rollout expands after Elon Musk says FSD progress is ahead of expectations",
                "created_at": "2026-04-05T16:25:00Z",
                "author_id": "u-3",
                "public_metrics": {
                    "like_count": 600,
                    "retweet_count": 150,
                    "reply_count": 70,
                    "quote_count": 20,
                },
            }
        ],
        "includes": {
            "users": [
                {
                    "id": "u-3",
                    "username": "elonmusk",
                    "verified": True,
                }
            ]
        },
    }
    feed = XRecentSearchStructuredEventFeed(
        "test-token",
        query="tesla robotaxi",
        session=_FakeSession(payload),
    )

    events = feed.get_recent_structured_events(
        Instrument("TSLA", AssetClass.STOCK),
        since=datetime(2026, 4, 5, 16, 0, tzinfo=timezone.utc),
        until=datetime(2026, 4, 5, 16, 30, tzinfo=timezone.utc),
    )

    assert len(events) == 1
    assert events[0].theme == "tsla_news"
    assert events[0].source == "x:@elonmusk"
    assert events[0].instrument_scope == ("TSLA",)
