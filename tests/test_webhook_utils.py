import pytest

from trading_bot.webhook_utils import (
    build_x_crc_response_token,
    parse_json_body,
    summarize_payload,
    verify_cron_secret,
    verify_finnhub_secret,
    verify_shared_secret,
    verify_x_webhook_signature,
)


def test_verify_finnhub_secret_accepts_matching_values() -> None:
    assert verify_finnhub_secret("abc123", "abc123") is True


def test_verify_finnhub_secret_rejects_missing_values() -> None:
    assert verify_finnhub_secret(None, "abc123") is False
    assert verify_finnhub_secret("abc123", None) is False


def test_verify_shared_secret_allows_missing_expected_secret() -> None:
    assert verify_shared_secret(None, None) is True
    assert verify_shared_secret("anything", "") is True


def test_verify_cron_secret_rejects_missing_expected_secret() -> None:
    assert verify_cron_secret("Bearer abc123", None) is False
    assert verify_cron_secret("abc123", "abc123") is False


def test_build_x_crc_response_token_matches_docs_example_shape() -> None:
    token = build_x_crc_response_token("challenge-token", "consumer-secret")
    assert token.startswith("sha256=")
    assert len(token) > len("sha256=")


def test_verify_x_webhook_signature_accepts_matching_hmac() -> None:
    payload = b'{"data":{"id":"1","text":"btc"}}'
    import base64
    import hashlib
    import hmac

    expected = "sha256=" + base64.b64encode(
        hmac.new(b"consumer-secret", payload, hashlib.sha256).digest()
    ).decode("utf-8")
    assert verify_x_webhook_signature(payload, expected, "consumer-secret") is True
    assert verify_x_webhook_signature(payload, build_x_crc_response_token("wrong", "consumer-secret"), "consumer-secret") is False


def test_parse_json_body_raises_on_invalid_json() -> None:
    with pytest.raises(ValueError):
        parse_json_body(b"{not-json}")


def test_summarize_payload_handles_list_payloads() -> None:
    summary = summarize_payload([{"id": "evt-1", "headline": "Beat"}])

    assert summary["payload_type"] == "list"
    assert summary["event_count"] == 1
