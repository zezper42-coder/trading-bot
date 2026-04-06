from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator

import requests

from trading_bot.config import BotConfig
from trading_bot.state_store import build_state_store
from trading_bot.webhook_bridge import normalize_x_stream


DEFAULT_X_STREAM_FIELDS = {
    "expansions": "author_id",
    "tweet.fields": "author_id,created_at,lang,public_metrics",
    "user.fields": "id,name,username,verified",
}


def iter_stream_payloads(lines: Iterable[str | bytes]) -> Iterator[dict[str, Any]]:
    for raw_line in lines:
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace").strip()
        else:
            line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload


class XFilteredStreamClient:
    BASE_URL = "https://api.x.com/2"

    def __init__(self, bearer_token: str, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
                "User-Agent": "trading-bot/0.1",
            }
        )

    def list_rules(self) -> tuple[dict[str, Any], ...]:
        response = self.session.get(f"{self.BASE_URL}/tweets/search/stream/rules", timeout=20)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if isinstance(data, list):
            return tuple(item for item in data if isinstance(item, dict))
        return ()

    def add_rule(self, value: str, tag: str) -> dict[str, Any]:
        response = self.session.post(
            f"{self.BASE_URL}/tweets/search/stream/rules",
            json={"add": [{"value": value, "tag": tag}]},
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def delete_rules(self, rule_ids: tuple[str, ...]) -> dict[str, Any]:
        response = self.session.post(
            f"{self.BASE_URL}/tweets/search/stream/rules",
            json={"delete": {"ids": list(rule_ids)}},
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def ensure_rule(self, value: str, tag: str) -> None:
        rules = self.list_rules()
        exact_match = any(
            str(rule.get("value") or "") == value and str(rule.get("tag") or "") == tag
            for rule in rules
        )
        stale_rule_ids = tuple(
            str(rule["id"])
            for rule in rules
            if rule.get("id") is not None
            and str(rule.get("tag") or "") == tag
            and str(rule.get("value") or "") != value
        )
        if stale_rule_ids:
            self.delete_rules(stale_rule_ids)
        if not exact_match:
            self.add_rule(value, tag)

    def iter_filtered_stream(
        self,
        *,
        fields: dict[str, str] | None = None,
        connect_timeout_seconds: int = 10,
        read_timeout_seconds: int = 90,
    ) -> Iterator[dict[str, Any]]:
        response = self.session.get(
            f"{self.BASE_URL}/tweets/search/stream",
            params=fields or DEFAULT_X_STREAM_FIELDS,
            stream=True,
            timeout=(connect_timeout_seconds, read_timeout_seconds),
        )
        response.raise_for_status()
        with response:
            yield from iter_stream_payloads(response.iter_lines())


class XFilteredStreamWorker:
    def __init__(
        self,
        config: BotConfig,
        *,
        client: XFilteredStreamClient | None = None,
        event_runner: Callable[..., dict[str, Any]] | None = None,
        state_store=None,
    ) -> None:
        if not config.x_bearer_token:
            raise RuntimeError("X_BEARER_TOKEN må være satt for filtered stream.")
        self.config = config
        self.execution_config = replace(config, x_recent_search_enabled=False)
        self.client = client or XFilteredStreamClient(config.x_bearer_token)
        if event_runner is None:
            from trading_bot.serverless import run_serverless_news_shock

            self.event_runner = run_serverless_news_shock
        else:
            self.event_runner = event_runner
        self.state_store = state_store or build_state_store(config)
        self.logger = logging.getLogger("trading_bot.x_stream")

    def ensure_stream_rule(self) -> None:
        self.client.ensure_rule(
            value=self.config.x_filtered_stream_rule,
            tag=self.config.x_filtered_stream_rule_tag,
        )

    def handle_payload(
        self,
        payload: dict[str, Any],
        *,
        received_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        if received_at is None:
            received_at = datetime.now(timezone.utc)
        events = normalize_x_stream(payload, received_at=received_at)
        if not events:
            return None
        summary = self.event_runner(
            self.execution_config,
            triggering_events=events,
        )
        self.state_store.record_heartbeat(
            status="ok",
            strategy="x_filtered_stream",
            details={
                "event_count": len(events),
                "event_ids": [event.event_id for event in events[:5]],
                "sources": [event.source for event in events[:5]],
                "themes": sorted({event.theme for event in events if event.theme}),
                "trade_summary": summary,
            },
        )
        return summary

    def run_forever(self) -> None:
        self.ensure_stream_rule()
        backoff_seconds = 1
        while True:
            try:
                self.state_store.record_heartbeat(
                    status="connecting",
                    strategy="x_filtered_stream",
                    details={
                        "rule_tag": self.config.x_filtered_stream_rule_tag,
                        "rule_preview": self.config.x_filtered_stream_rule[:140],
                    },
                )
                self.logger.info("Kobler til X filtered stream.")
                for payload in self.client.iter_filtered_stream(
                    connect_timeout_seconds=self.config.x_stream_connect_timeout_seconds,
                    read_timeout_seconds=self.config.x_stream_read_timeout_seconds,
                ):
                    backoff_seconds = 1
                    try:
                        summary = self.handle_payload(payload)
                    except Exception as exc:  # pragma: no cover - protective logging
                        self.logger.exception("Feil under behandling av X-stream event: %s", exc)
                        self.state_store.record_heartbeat(
                            status="error",
                            strategy="x_filtered_stream",
                            details={"error": str(exc)},
                        )
                        continue
                    if summary is not None:
                        self.logger.info("X-stream behandlet event. ran=%s", summary.get("ran"))
                self.logger.warning("X filtered stream lukket. Kobler opp på nytt.")
            except requests.RequestException as exc:
                self.logger.warning("X filtered stream feilet: %s", exc)
                self.state_store.record_heartbeat(
                    status="reconnecting",
                    strategy="x_filtered_stream",
                    details={
                        "error": str(exc),
                        "backoff_seconds": backoff_seconds,
                    },
                )
            time.sleep(backoff_seconds)
            backoff_seconds = min(self.config.x_stream_max_backoff_seconds, backoff_seconds * 2)
