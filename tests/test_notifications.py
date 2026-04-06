from datetime import datetime, timezone

from trading_bot.domain import AssetClass, Instrument, OrderPlan, OrderSide, Signal, SignalAction
from trading_bot.notifications import TelegramNotifier


class DummyResponse:
    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self.payload = payload or {"ok": True, "result": {"message_id": 1}}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class DummySession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], float]] = []

    def post(self, url: str, json: dict[str, object], timeout: float) -> DummyResponse:
        self.calls.append((url, json, timeout))
        return DummyResponse()


def test_telegram_notifier_sends_html_message_for_order_updates() -> None:
    session = DummySession()
    notifier = TelegramNotifier(
        bot_token="bot-token",
        chat_id="-100123",
        disable_notification=True,
        message_thread_id=42,
        session=session,
    )
    signal = Signal(
        instrument=Instrument("BTC/USD", AssetClass.CRYPTO),
        action=SignalAction.BUY,
        price=66_000.5,
        reason="Unexpected BTC news",
        event_id="evt-btc-1",
        source="finnhub-webhook",
        anchor_price=65_900.0,
        stop_price=65_500.0,
    )
    plan = OrderPlan(
        instrument=signal.instrument,
        side=OrderSide.BUY,
        qty=0.015,
        notional=990.01,
        capped_by_buying_power=True,
        event_id=signal.event_id,
        signal_reason=signal.reason,
    )

    notifier.send_order_update(
        signal=signal,
        plan=plan,
        timestamp=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
        order_id="dry-run",
        dry_run=True,
    )

    assert len(session.calls) == 1
    url, payload, timeout = session.calls[0]
    assert url == "https://api.telegram.org/botbot-token/sendMessage"
    assert payload["chat_id"] == "-100123"
    assert payload["parse_mode"] == "HTML"
    assert payload["disable_notification"] is True
    assert payload["message_thread_id"] == 42
    assert "DRY RUN BUY BTC/USD" in str(payload["text"])
    assert "Begrunnelse: Unexpected BTC news" in str(payload["text"])
    assert "Buying power cap" in str(payload["text"])
    assert timeout == 10.0


def test_telegram_notifier_omits_thread_id_when_not_set() -> None:
    session = DummySession()
    notifier = TelegramNotifier(
        bot_token="bot-token",
        chat_id="@alerts",
        session=session,
    )
    signal = Signal(
        instrument=Instrument("TSLA", AssetClass.STOCK),
        action=SignalAction.SELL,
        price=201.25,
        reason="Trailing stop hit",
        exit_reason="trailing_stop",
    )
    plan = OrderPlan(
        instrument=signal.instrument,
        side=OrderSide.SELL,
        qty=2,
        signal_reason=signal.reason,
    )

    notifier.send_order_update(
        signal=signal,
        plan=plan,
        timestamp=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
        order_id="order-123",
        dry_run=False,
    )

    _, payload, _ = session.calls[0]
    assert "message_thread_id" not in payload
    assert "<b>SELL TSLA</b>" in str(payload["text"])
    assert "Begrunnelse: Trailing stop hit" in str(payload["text"])


def test_telegram_notifier_can_reply_to_specific_chat_and_message() -> None:
    session = DummySession()
    notifier = TelegramNotifier(
        bot_token="bot-token",
        chat_id="@alerts",
        session=session,
    )

    notifier.send_text_message(
        text="<b>hei</b>",
        chat_id="12345",
        reply_to_message_id=88,
        message_thread_id=9,
    )

    _, payload, _ = session.calls[0]
    assert payload["chat_id"] == "12345"
    assert payload["message_thread_id"] == 9
    assert payload["reply_parameters"] == {"message_id": 88}
