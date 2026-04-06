from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import requests


DEFAULT_X_WEBHOOK_FIELDS = {
    "expansions": "author_id",
    "tweet.fields": "author_id,created_at,lang,public_metrics",
    "user.fields": "id,name,username,verified",
}


@dataclass(frozen=True)
class XWebhookSetupResult:
    webhook_id: str
    webhook_url: str
    created: bool
    validated: bool
    rule_value: str
    rule_tag: str
    linked: bool


class XWebhookClient:
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

    def list_webhooks(self) -> tuple[dict, ...]:
        response = self.session.get(f"{self.BASE_URL}/webhooks", timeout=20)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if isinstance(data, list):
            return tuple(item for item in data if isinstance(item, dict))
        meta = payload.get("meta")
        if isinstance(meta, dict) and meta.get("result_count") == 0:
            return ()
        return ()

    def create_webhook(self, url: str) -> dict:
        response = self.session.post(
            f"{self.BASE_URL}/webhooks",
            json={"url": url},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                return first
        return payload

    def validate_webhook(self, webhook_id: str) -> dict:
        response = self.session.put(f"{self.BASE_URL}/webhooks/{webhook_id}", timeout=20)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload

    def list_rules(self) -> tuple[dict, ...]:
        response = self.session.get(f"{self.BASE_URL}/tweets/search/stream/rules", timeout=20)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if isinstance(data, list):
            return tuple(item for item in data if isinstance(item, dict))
        return ()

    def add_rule(self, value: str, tag: str) -> dict:
        response = self.session.post(
            f"{self.BASE_URL}/tweets/search/stream/rules",
            json={"add": [{"value": value, "tag": tag}]},
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def link_filtered_stream(
        self,
        webhook_id: str,
        *,
        fields: dict[str, str] | None = None,
    ) -> dict:
        query = urlencode(fields or DEFAULT_X_WEBHOOK_FIELDS)
        response = self.session.post(
            f"{self.BASE_URL}/tweets/search/webhooks/{webhook_id}?{query}",
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def ensure_filtered_stream_webhook(
        self,
        *,
        webhook_url: str,
        rule_value: str,
        rule_tag: str,
        fields: dict[str, str] | None = None,
    ) -> XWebhookSetupResult:
        existing_webhooks = self.list_webhooks()
        matched = next((item for item in existing_webhooks if item.get("url") == webhook_url), None)
        created = False
        if matched is None:
            matched = self.create_webhook(webhook_url)
            created = True
        webhook_id = str(matched["id"])

        validated_response = self.validate_webhook(webhook_id)
        validated = bool(validated_response.get("valid", True))

        rules = self.list_rules()
        has_rule = any(
            str(rule.get("value") or "") == rule_value and str(rule.get("tag") or "") == rule_tag
            for rule in rules
        )
        if not has_rule:
            self.add_rule(rule_value, rule_tag)

        self.link_filtered_stream(webhook_id, fields=fields)
        linked = True

        return XWebhookSetupResult(
            webhook_id=webhook_id,
            webhook_url=webhook_url,
            created=created,
            validated=validated,
            rule_value=rule_value,
            rule_tag=rule_tag,
            linked=linked,
        )
