from trading_bot.telegram_chat import (
    DEFAULT_OPENAI_MODEL,
    OpenAIChatResponder,
    TelegramActionRequest,
    TelegramIncomingMessage,
    TelegramRuntimeSnapshot,
    build_ai_runtime_context,
    build_local_command_response,
    extract_action_request,
    extract_openai_response_text,
    extract_telegram_message,
    format_action_response,
    is_authorized_telegram_chat,
    verify_telegram_secret,
)


class DummyResponse:
    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self.payload = payload or {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Hei fra OpenAI",
                        }
                    ]
                }
            ]
        }

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class DummySession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], dict[str, str], float]] = []

    def post(
        self,
        url: str,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> DummyResponse:
        self.calls.append((url, json, headers, timeout))
        return DummyResponse()


def test_extract_telegram_message_reads_text_payload() -> None:
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 77,
            "text": "hei bot",
            "chat": {"id": 12345, "type": "private"},
            "from": {"id": 9, "first_name": "Jesper", "is_bot": False},
        },
    }

    message = extract_telegram_message(payload)

    assert message is not None
    assert message.chat_id == "12345"
    assert message.message_id == 77
    assert message.text == "hei bot"
    assert message.display_name == "Jesper"


def test_extract_telegram_message_ignores_bot_sender() -> None:
    payload = {
        "message": {
            "message_id": 1,
            "text": "ignore me",
            "chat": {"id": 1},
            "from": {"is_bot": True},
        }
    }

    assert extract_telegram_message(payload) is None


def test_build_local_command_response_returns_help_and_status() -> None:
    message = TelegramIncomingMessage(chat_id="42", message_id=1, text="/status")
    snapshot = TelegramRuntimeSnapshot(
        authorized=True,
        dashboard_url="https://example.com/dashboard",
        account={"equity": 100000, "cash": 90000, "buying_power": 180000},
        control={"bot_enabled": True, "dry_run_override": False, "emergency_stop_active": False},
    )

    response = build_local_command_response(
        message,
        ai_enabled=False,
        model=DEFAULT_OPENAI_MODEL,
        snapshot=snapshot,
    )

    assert response is not None
    assert "mangler OPENAI_API_KEY" in response
    assert "42" in response
    assert "100000" in response


def test_build_local_command_response_formats_positions() -> None:
    message = TelegramIncomingMessage(chat_id="42", message_id=1, text="/positions")
    snapshot = TelegramRuntimeSnapshot(
        authorized=True,
        positions=(
            {
                "symbol": "BTC/USD",
                "qty": 0.25,
                "avg_entry_price": 66700,
                "market_value": 16800,
            },
        ),
    )

    response = build_local_command_response(
        message,
        ai_enabled=True,
        model=DEFAULT_OPENAI_MODEL,
        snapshot=snapshot,
    )

    assert response is not None
    assert "BTC/USD" in response
    assert "16800" in response


def test_verify_telegram_secret_accepts_missing_expected_secret() -> None:
    assert verify_telegram_secret(None, None) is True
    assert verify_telegram_secret("abc", "") is True
    assert verify_telegram_secret("abc", "abc") is True
    assert verify_telegram_secret("abc", "xyz") is False


def test_extract_openai_response_text_uses_output_chunks() -> None:
    payload = {
        "output": [
            {
                "content": [
                    {"type": "output_text", "text": "Linje 1"},
                    {"type": "output_text", "text": "Linje 2"},
                ]
            }
        ]
    }

    assert extract_openai_response_text(payload) == "Linje 1\n\nLinje 2"


def test_openai_chat_responder_calls_responses_api() -> None:
    session = DummySession()
    responder = OpenAIChatResponder(
        api_key="openai-key",
        model="gpt-4.1-mini",
        session=session,
    )
    message = TelegramIncomingMessage(chat_id="42", message_id=1, text="Hva gjør boten?")

    reply = responder.generate_reply(message, runtime_context="Runtime-kontekst:\n{\"positions\":[]}")

    assert reply == "Hei fra OpenAI"
    assert len(session.calls) == 1
    url, payload, headers, timeout = session.calls[0]
    assert url == "https://api.openai.com/v1/responses"
    assert payload["model"] == "gpt-4.1-mini"
    assert "Hva gjør boten?" in str(payload["input"])
    assert "Runtime-kontekst" in str(payload["input"])
    assert headers["Authorization"] == "Bearer openai-key"
    assert timeout == 20.0


def test_extract_action_request_maps_control_commands() -> None:
    message = TelegramIncomingMessage(chat_id="42", message_id=1, text="/panic")

    request = extract_action_request(message, snapshot=TelegramRuntimeSnapshot(authorized=True))

    assert request == TelegramActionRequest("emergency-liquidate", {})


def test_format_action_response_formats_emergency_liquidate() -> None:
    text = format_action_response(
        TelegramActionRequest("emergency-liquidate", {}),
        {"ok": True, "cancelled": [{"id": "1"}], "closed": [{"symbol": "BTC/USD"}]},
    )

    assert "Nødselg utført" in text
    assert "1" in text


def test_is_authorized_telegram_chat_matches_expected_chat() -> None:
    assert is_authorized_telegram_chat("-100123", "-100123") is True
    assert is_authorized_telegram_chat("-100123", "42") is False


def test_build_ai_runtime_context_serializes_snapshot() -> None:
    snapshot = TelegramRuntimeSnapshot(
        authorized=True,
        dashboard_url="https://example.com/dashboard",
        account={"equity": 1000},
        positions=({"symbol": "BTC/USD"},),
    )

    context = build_ai_runtime_context(snapshot)

    assert "example.com/dashboard" in context
    assert "BTC/USD" in context
