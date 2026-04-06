from dataclasses import replace
from datetime import datetime, timezone

from trading_bot.config import load_config
from trading_bot.x_stream import XFilteredStreamWorker, iter_stream_payloads


class _FakeStateStore:
    def __init__(self) -> None:
        self.heartbeats: list[dict[str, object]] = []

    def record_heartbeat(self, *, status: str, strategy: str, details: dict[str, object]) -> None:
        self.heartbeats.append(
            {
                "status": status,
                "strategy": strategy,
                "details": details,
            }
        )


def test_iter_stream_payloads_skips_keepalives_and_invalid_lines() -> None:
    payloads = list(
        iter_stream_payloads(
            [
                b"",
                b"   ",
                b'{"data":{"id":"1"}}',
                b"not-json",
                '{"data":{"id":"2"}}',
            ]
        )
    )

    assert payloads == [{"data": {"id": "1"}}, {"data": {"id": "2"}}]


def test_x_stream_worker_normalizes_stream_payload_and_disables_recent_search_for_execution() -> None:
    captured: dict[str, object] = {}

    def fake_event_runner(config, *, triggering_events):
        captured["config"] = config
        captured["events"] = tuple(triggering_events)
        return {"ok": True, "ran": True}

    config = replace(
        load_config(),
        x_bearer_token="test-token",
        x_recent_search_enabled=True,
    )
    state_store = _FakeStateStore()
    worker = XFilteredStreamWorker(
        config,
        event_runner=fake_event_runner,
        state_store=state_store,
    )
    payload = {
        "data": {
            "id": "stream-post-1",
            "text": "Bitcoin reserve rumors intensify after unexpected treasury comments",
            "created_at": "2026-04-06T07:10:00Z",
            "author_id": "u-1",
            "public_metrics": {
                "like_count": 200,
                "retweet_count": 40,
                "reply_count": 8,
                "quote_count": 3,
            },
        },
        "includes": {
            "users": [
                {
                    "id": "u-1",
                    "username": "watcherguru",
                    "verified": True,
                }
            ]
        },
        "matching_rules": [{"id": "rule-1", "tag": "trading-bot-realtime"}],
    }

    summary = worker.handle_payload(payload, received_at=datetime(2026, 4, 6, 7, 10, 2, tzinfo=timezone.utc))

    assert summary == {"ok": True, "ran": True}
    assert captured["config"].x_recent_search_enabled is False
    events = captured["events"]
    assert len(events) == 1
    assert events[0].source == "x_stream:@watcherguru"
    assert events[0].theme == "btc_news"
    assert state_store.heartbeats[-1]["strategy"] == "x_filtered_stream"
