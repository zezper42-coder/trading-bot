from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any


def verify_cron_secret(authorization_header: str | None, expected_secret: str | None) -> bool:
    if not expected_secret:
        return False
    if not authorization_header:
        return False
    prefix = "Bearer "
    if not authorization_header.startswith(prefix):
        return False
    token = authorization_header[len(prefix) :].strip()
    if not token:
        return False
    return hmac.compare_digest(token, expected_secret)


def verify_shared_secret(received_secret: str | None, expected_secret: str | None) -> bool:
    if not expected_secret:
        return True
    if not received_secret:
        return False
    return hmac.compare_digest(received_secret, expected_secret)


def verify_finnhub_secret(received_secret: str | None, expected_secret: str | None) -> bool:
    if not received_secret or not expected_secret:
        return False
    return verify_shared_secret(received_secret, expected_secret)


def build_x_crc_response_token(crc_token: str, consumer_secret: str) -> str:
    digest = hmac.new(
        consumer_secret.encode("utf-8"),
        crc_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return "sha256=" + base64.b64encode(digest).decode("utf-8")


def verify_x_webhook_signature(
    raw_body: bytes,
    signature_header: str | None,
    consumer_secret: str | None,
) -> bool:
    if not raw_body or not signature_header or not consumer_secret:
        return False
    expected = "sha256=" + base64.b64encode(
        hmac.new(
            consumer_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    return hmac.compare_digest(expected, signature_header)


def parse_json_body(raw_body: bytes) -> Any:
    if not raw_body:
        raise ValueError("Request body is empty.")
    try:
        return json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Request body is not valid JSON.") from exc


def summarize_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        return {
            "payload_type": "list",
            "event_count": len(payload),
            "first_item_keys": sorted(payload[0].keys()) if payload and isinstance(payload[0], dict) else [],
        }
    if isinstance(payload, dict):
        return {
            "payload_type": "dict",
            "keys": sorted(payload.keys()),
        }
    return {
        "payload_type": type(payload).__name__,
    }
