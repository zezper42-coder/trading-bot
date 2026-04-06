from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from typing import Any

import requests

from trading_bot.notifications import TelegramNotifier
from trading_bot.webhook_utils import verify_shared_secret

TELEGRAM_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"

SYSTEM_PROMPT = """Du er Telegram-assistenten for Jesper sin trading-bot.
Svar kort, konkret og på norsk som standard.

Du har tilgang til runtime-kontekst fra boten når den er tilgjengelig:
- konto, equity, cash og buying power
- kontrollstate som bot enabled, dry run og heartbeat
- åpne posisjoner
- siste nyheter, signaler og ordre
- strategi-settings per tema

Viktige begrensninger:
- Bruk bare den runtime-konteksten du faktisk får i meldingen. Hvis noe mangler eller er tomt, si det tydelig.
- Du skal ikke late som du har sett nyere marked eller nyheter enn konteksten viser.
- Du skal ikke påstå at en trade er gjort hvis ordrelisten ikke viser det.
- Hvis brukeren ber deg gjøre kodeendringer eller større systemendringer, si at det må gjøres i Codex-prosjektet.

Hold svarene nyttige og uten fluff."""

HELP_TEXT = """<b>Telegram-chat er koblet</b>

Jeg kan både svare med live bot-kontekst og styre boten herfra.

Kommandoer:
- <code>/help</code>
- <code>/ping</code>
- <code>/status</code>
- <code>/id</code>
- <code>/positions</code>
- <code>/orders</code>
- <code>/signals</code>
- <code>/events</code>
- <code>/settings</code>
- <code>/stop</code>
- <code>/resume</code>
- <code>/dryrun</code>
- <code>/live</code>
- <code>/panic</code>
- <code>/cancel</code>
- <code>/scan</code>
- <code>/eval</code>

Vanlig fritekst fungerer også når <code>OPENAI_API_KEY</code> er satt på serveren."""

UNAUTHORIZED_TEXT = (
    "Denne Telegram-chatten er ikke autorisert for full bot-kontroll. "
    "Bruk den chatten som er satt som <code>TELEGRAM_CHAT_ID</code>."
)


@dataclass(frozen=True)
class TelegramIncomingMessage:
    chat_id: str
    message_id: int | None
    text: str
    first_name: str | None = None
    username: str | None = None
    chat_type: str | None = None
    thread_id: int | None = None

    @property
    def display_name(self) -> str:
        if self.first_name:
            return self.first_name
        if self.username:
            return self.username
        return "bruker"


@dataclass(frozen=True)
class TelegramRuntimeSnapshot:
    authorized: bool
    dashboard_url: str | None = None
    account: dict[str, Any] | None = None
    control: dict[str, Any] | None = None
    heartbeat: dict[str, Any] | None = None
    positions: tuple[dict[str, Any], ...] = ()
    orders: tuple[dict[str, Any], ...] = ()
    signals: tuple[dict[str, Any], ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    settings: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class TelegramActionRequest:
    action: str
    payload: dict[str, Any]


def verify_telegram_secret(received_secret: str | None, expected_secret: str | None) -> bool:
    return verify_shared_secret(received_secret, expected_secret)


def extract_telegram_message(payload: Any) -> TelegramIncomingMessage | None:
    if not isinstance(payload, dict):
        return None
    message = payload.get("message") or payload.get("edited_message")
    if not isinstance(message, dict):
        return None
    sender = message.get("from")
    if isinstance(sender, dict) and sender.get("is_bot") is True:
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("id") is None:
        return None
    thread_id = message.get("message_thread_id")
    if thread_id is not None:
        thread_id = int(thread_id)
    return TelegramIncomingMessage(
        chat_id=str(chat["id"]),
        message_id=int(message["message_id"]) if message.get("message_id") is not None else None,
        text=text.strip(),
        first_name=sender.get("first_name") if isinstance(sender, dict) else None,
        username=sender.get("username") if isinstance(sender, dict) else None,
        chat_type=str(chat.get("type")) if chat.get("type") is not None else None,
        thread_id=thread_id,
    )


def build_local_command_response(
    message: TelegramIncomingMessage,
    *,
    ai_enabled: bool,
    model: str,
    snapshot: TelegramRuntimeSnapshot | None = None,
) -> str | None:
    command = message.text.strip().split(maxsplit=1)[0].lower()
    if command in {"/start", "/help"}:
        return HELP_TEXT
    if command == "/ping":
        return "pong"
    if command == "/id":
        return f"Din chat-id er <code>{escape(message.chat_id)}</code>"
    if snapshot is not None and not snapshot.authorized and command not in {"/help", "/start", "/ping", "/id"}:
        return UNAUTHORIZED_TEXT
    if command == "/status":
        ai_status = "aktiv" if ai_enabled else "mangler OPENAI_API_KEY"
        account_line = ""
        if snapshot and snapshot.account and "equity" in snapshot.account:
            account_line = (
                f"\nEquity: <code>{_format_float(snapshot.account.get('equity'))}</code>"
                f"\nCash: <code>{_format_float(snapshot.account.get('cash'))}</code>"
                f"\nBuying power: <code>{_format_float(snapshot.account.get('buying_power'))}</code>"
            )
        control_line = ""
        if snapshot and snapshot.control:
            control_line = (
                f"\nBot enabled: <code>{escape(str(snapshot.control.get('bot_enabled')))}</code>"
                f"\nDry run: <code>{escape(str(snapshot.control.get('dry_run_override')))}</code>"
                f"\nEmergency stop: <code>{escape(str(snapshot.control.get('emergency_stop_active')))}</code>"
            )
        dashboard_line = ""
        if snapshot and snapshot.dashboard_url:
            dashboard_line = f"\nDashboard: <code>{escape(snapshot.dashboard_url)}</code>"
        return (
            "<b>Telegram Chat Status</b>\n"
            f"Chat-ID: <code>{escape(message.chat_id)}</code>\n"
            f"AI-svar: <code>{escape(ai_status)}</code>\n"
            f"Modell: <code>{escape(model)}</code>\n"
            f"Webhooken kan svare på meldinger i denne chatten.{account_line}{control_line}{dashboard_line}"
        )
    if command == "/positions":
        return _format_positions_response(snapshot)
    if command == "/orders":
        return _format_orders_response(snapshot)
    if command == "/signals":
        return _format_signals_response(snapshot)
    if command == "/events":
        return _format_events_response(snapshot)
    if command == "/settings":
        return _format_settings_response(snapshot)
    return None


def is_authorized_telegram_chat(configured_chat_id: str | None, incoming_chat_id: str) -> bool:
    if not configured_chat_id:
        return True
    return configured_chat_id.strip() == incoming_chat_id.strip()


def extract_action_request(
    message: TelegramIncomingMessage,
    *,
    snapshot: TelegramRuntimeSnapshot | None = None,
) -> TelegramActionRequest | None:
    command = message.text.strip().split(maxsplit=1)[0].lower()
    if snapshot is not None and not snapshot.authorized:
        return None
    action_map = {
        "/stop": TelegramActionRequest("stop", {}),
        "/resume": TelegramActionRequest("resume", {}),
        "/dryrun": TelegramActionRequest("dry-run", {"dry_run": True}),
        "/live": TelegramActionRequest("dry-run", {"dry_run": False}),
        "/panic": TelegramActionRequest("emergency-liquidate", {}),
        "/cancel": TelegramActionRequest("cancel-open-orders", {}),
        "/scan": TelegramActionRequest("run-earnings-scan", {}),
        "/eval": TelegramActionRequest("run-news-eval", {}),
    }
    return action_map.get(command)


def format_action_response(request: TelegramActionRequest, result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return "Kommandoen feilet."
    if request.action == "stop":
        return "<b>Bot stoppet</b>\nNye entries er blokkert."
    if request.action == "resume":
        return "<b>Bot gjenopptatt</b>\nNye entries er tillatt igjen."
    if request.action == "dry-run":
        dry_run = bool(request.payload.get("dry_run", True))
        mode = "på" if dry_run else "av"
        return f"<b>Dry run er nå {mode}</b>"
    if request.action == "emergency-liquidate":
        cancelled = len(result.get("cancelled") or [])
        closed = len(result.get("closed") or [])
        return (
            "<b>Nødselg utført</b>\n"
            f"Kansellerte ordre: <code>{cancelled}</code>\n"
            f"Lukkede posisjoner: <code>{closed}</code>"
        )
    if request.action == "cancel-open-orders":
        cancelled = len(result.get("cancelled") or [])
        return f"<b>Pending ordre kansellert</b>\nAntall: <code>{cancelled}</code>"
    if request.action == "run-earnings-scan":
        summary = result.get("summary") or {}
        tracked = summary.get("tracked_count")
        return f"<b>Earnings-scan kjørt</b>\nTracked: <code>{escape(str(tracked))}</code>"
    if request.action == "run-news-eval":
        summary = result.get("summary") or {}
        event_count = summary.get("event_count")
        return f"<b>News-evaluering kjørt</b>\nEvents vurdert: <code>{escape(str(event_count))}</code>"
    return "<b>Kommando utført</b>"


def build_ai_runtime_context(snapshot: TelegramRuntimeSnapshot | None) -> str:
    if snapshot is None:
        return "Runtime-kontekst: ikke tilgjengelig."
    payload = {
        "authorized": snapshot.authorized,
        "dashboard_url": snapshot.dashboard_url,
        "account": snapshot.account,
        "control": snapshot.control,
        "heartbeat": snapshot.heartbeat,
        "positions": list(snapshot.positions[:10]),
        "orders": list(snapshot.orders[:10]),
        "signals": list(snapshot.signals[:10]),
        "events": list(snapshot.events[:10]),
        "settings": list(snapshot.settings),
    }
    return "Runtime-kontekst:\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


@dataclass
class OpenAIChatResponder:
    api_key: str | None
    model: str = DEFAULT_OPENAI_MODEL
    system_prompt: str = SYSTEM_PROMPT
    timeout_seconds: float = 20.0
    session: requests.Session | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def _session(self) -> requests.Session:
        return self.session or requests.Session()

    def generate_reply(
        self,
        message: TelegramIncomingMessage,
        *,
        runtime_context: str | None = None,
    ) -> str:
        if not self.enabled:
            return (
                "Telegram-chatten er koblet, men <code>OPENAI_API_KEY</code> mangler på serveren. "
                "Foreløpig kan du bruke <code>/help</code>, <code>/status</code> og <code>/id</code>."
            )
        context_block = runtime_context or "Runtime-kontekst: ikke tilgjengelig."

        response = self._session.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "instructions": self.system_prompt,
                "input": (
                    f"Telegram chat type: {message.chat_type or 'unknown'}\n"
                    f"Telegram user: {message.display_name}\n"
                    f"{context_block}\n\n"
                    f"Message:\n{message.text}"
                ),
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        text = extract_openai_response_text(data)
        if not text:
            raise RuntimeError("OpenAI svarte uten tekst.")
        return escape(text)


def extract_openai_response_text(payload: dict[str, Any]) -> str:
    direct_text = payload.get("output_text")
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text.strip()

    chunks: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            content_type = content.get("type")
            if content_type in {"output_text", "text"}:
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
            elif content_type == "refusal":
                refusal = content.get("refusal")
                if isinstance(refusal, str) and refusal.strip():
                    chunks.append(refusal.strip())
    return "\n\n".join(chunks).strip()


def reply_to_telegram_message(
    *,
    bot_token: str,
    incoming_message: TelegramIncomingMessage,
    text: str,
    disable_notification: bool = False,
    session: requests.Session | None = None,
) -> None:
    notifier = TelegramNotifier(
        bot_token=bot_token,
        chat_id=incoming_message.chat_id,
        disable_notification=disable_notification,
        message_thread_id=incoming_message.thread_id,
        session=session,
    )
    notifier.send_text_message(
        text=text,
        reply_to_message_id=incoming_message.message_id,
        chat_id=incoming_message.chat_id,
    )


def _format_positions_response(snapshot: TelegramRuntimeSnapshot | None) -> str:
    if snapshot is None:
        return "Ingen runtime-kontekst tilgjengelig."
    if not snapshot.positions:
        return "<b>Åpne posisjoner</b>\nIngen åpne posisjoner."
    lines = ["<b>Åpne posisjoner</b>"]
    for row in snapshot.positions[:8]:
        lines.append(
            f"- <code>{escape(str(row.get('symbol')))}</code> qty <code>{_format_float(row.get('qty'))}</code>, "
            f"entry <code>{_format_float(row.get('avg_entry_price'))}</code>, "
            f"verdi <code>{_format_float(row.get('market_value'))}</code>"
        )
    return "\n".join(lines)


def _format_orders_response(snapshot: TelegramRuntimeSnapshot | None) -> str:
    if snapshot is None:
        return "Ingen runtime-kontekst tilgjengelig."
    if not snapshot.orders:
        return "<b>Siste ordre</b>\nIngen ordre logget ennå."
    lines = ["<b>Siste ordre</b>"]
    for row in snapshot.orders[:8]:
        lines.append(
            f"- <code>{escape(str(row.get('side')).upper())}</code> <code>{escape(str(row.get('symbol')))}</code> "
            f"status <code>{escape(str(row.get('status')))}</code> "
            f"pris <code>{_format_float(row.get('price'))}</code>"
        )
    return "\n".join(lines)


def _format_signals_response(snapshot: TelegramRuntimeSnapshot | None) -> str:
    if snapshot is None:
        return "Ingen runtime-kontekst tilgjengelig."
    if not snapshot.signals:
        return "<b>Siste signaler</b>\nIngen signaler logget ennå."
    lines = ["<b>Siste signaler</b>"]
    for row in snapshot.signals[:8]:
        lines.append(
            f"- <code>{escape(str(row.get('action')).upper())}</code> <code>{escape(str(row.get('symbol')))}</code> "
            f"tema <code>{escape(str(row.get('theme') or 'n/a'))}</code> "
            f"grunn: {escape(str(row.get('reason') or ''))}"
        )
    return "\n".join(lines)


def _format_events_response(snapshot: TelegramRuntimeSnapshot | None) -> str:
    if snapshot is None:
        return "Ingen runtime-kontekst tilgjengelig."
    if not snapshot.events:
        return "<b>Siste nyheter</b>\nIngen events logget ennå."
    lines = ["<b>Siste nyheter</b>"]
    for row in snapshot.events[:8]:
        lines.append(
            f"- <code>{escape(str(row.get('theme') or 'general'))}</code> "
            f"{escape(str(row.get('headline') or ''))}"
        )
    return "\n".join(lines)


def _format_settings_response(snapshot: TelegramRuntimeSnapshot | None) -> str:
    if snapshot is None:
        return "Ingen runtime-kontekst tilgjengelig."
    if not snapshot.settings:
        return "<b>Settings</b>\nIngen settings funnet."
    lines = ["<b>Strategi-settings</b>"]
    for row in snapshot.settings:
        lines.append(
            f"- <code>{escape(str(row.get('theme')))}</code>: "
            f"enabled <code>{escape(str(row.get('enabled')))}</code>, "
            f"risk/trade <code>{_format_float(row.get('risk_per_trade'))}</code>, "
            f"min surprise <code>{_format_float(row.get('min_surprise'))}</code>, "
            f"trade score <code>{_format_float(row.get('min_trade_score'))}</code>"
        )
    return "\n".join(lines)


def _format_float(value: Any) -> str:
    if value in {None, ""}:
        return "—"
    try:
        return f"{float(value):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return escape(str(value))
