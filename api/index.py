from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trading_bot.config import load_config
from trading_bot.dashboard import (
    build_dashboard_state_payload,
    build_positions_payload,
    build_settings_payload,
    dashboard_logout_cookie,
    dashboard_session_cookie,
    perform_dashboard_action,
    render_dashboard_page,
    update_settings_payload,
    verify_dashboard_session,
)
from trading_bot.state_store import build_state_store
from trading_bot.serverless import (
    run_serverless_earnings_once,
    run_serverless_earnings_scan,
    run_serverless_news_shock,
)
from trading_bot.webhook_utils import (
    build_x_crc_response_token,
    parse_json_body,
    summarize_payload,
    verify_cron_secret,
    verify_finnhub_secret,
    verify_x_webhook_signature,
)
from trading_bot.webhook_bridge import (
    VERCEL_STRUCTURED_EVENT_LOG,
    normalize_finnhub_webhook,
    normalize_x_webhook,
    structured_event_to_record,
)
from trading_bot.telegram_chat import (
    DEFAULT_OPENAI_MODEL,
    TELEGRAM_SECRET_HEADER,
    OpenAIChatResponder,
    TelegramRuntimeSnapshot,
    build_local_command_response,
    build_ai_runtime_context,
    extract_telegram_message,
    extract_action_request,
    format_action_response,
    is_authorized_telegram_chat,
    reply_to_telegram_message,
    verify_telegram_secret,
)


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/dashboard":
            self._handle_dashboard_get()
            return
        if path.startswith("/api/ui/"):
            self._handle_ui_get(path)
            return
        if path == "/api/x-webhook":
            self._handle_x_webhook_get()
            return
        if path == "/api/telegram":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "telegram-chat-webhook",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            return
        if path == "/api/cron/news-shock":
            self._handle_cron_news_shock()
            return
        if path == "/api/cron/earnings-scan":
            self._handle_cron_earnings_scan()
            return
        if path == "/api/cron/earnings-run":
            self._handle_cron_earnings_run()
            return
        self._send_json(
            200,
            {
                "ok": True,
                "service": "finnhub-webhook",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/ui/"):
            self._handle_ui_post(path)
            return
        if path == "/api/x-webhook":
            self._handle_x_webhook_post()
            return
        if path == "/api/telegram":
            self._handle_telegram_post()
            return
        self._handle_finnhub_post()

    def _handle_dashboard_get(self) -> None:
        config = load_config()
        if not config.dashboard_admin_password:
            self._send_html(
                503,
                "<!doctype html><html><body><h1>DASHBOARD_ADMIN_PASSWORD mangler</h1></body></html>",
            )
            return
        authenticated = verify_dashboard_session(
            self.headers.get("Cookie"),
            config.dashboard_admin_password,
        )
        self._send_html(200, render_dashboard_page(config, authenticated=authenticated))

    def _handle_ui_get(self, path: str) -> None:
        config = load_config()
        if not self._require_ui_auth(config):
            return
        state_store = build_state_store(config)

        if path == "/api/ui/state":
            self._send_json(200, build_dashboard_state_payload(config))
            return
        if path == "/api/ui/positions":
            self._send_json(200, {"ok": True, "rows": build_positions_payload(config)})
            return
        if path == "/api/ui/orders":
            self._send_json(200, {"ok": True, "rows": state_store.list_recent_orders()})
            return
        if path == "/api/ui/events":
            self._send_json(200, {"ok": True, "rows": state_store.list_recent_events()})
            return
        if path == "/api/ui/signals":
            self._send_json(200, {"ok": True, "rows": state_store.list_recent_signals()})
            return
        if path == "/api/ui/settings":
            self._send_json(200, {"ok": True, "rows": build_settings_payload(config)})
            return
        self._send_json(404, {"ok": False, "error": "Unknown dashboard endpoint."})

    def _handle_ui_post(self, path: str) -> None:
        config = load_config()
        if path == "/api/ui/login":
            self._handle_ui_login(config)
            return

        if path == "/api/ui/logout":
            self._send_json(
                200,
                {"ok": True},
                headers={"Set-Cookie": dashboard_logout_cookie()},
            )
            return

        if not self._require_ui_auth(config):
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            payload = parse_json_body(raw_body) if raw_body else {}
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        if not isinstance(payload, dict):
            payload = {}

        if path == "/api/ui/settings":
            updated = update_settings_payload(config, payload)
            self._send_json(200, {"ok": True, "rows": updated})
            return

        control_prefix = "/api/ui/control/"
        if path.startswith(control_prefix):
            action = path[len(control_prefix) :]
            try:
                result = perform_dashboard_action(config, action, payload)
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        action_prefix = "/api/ui/action/"
        if path.startswith(action_prefix):
            action = path[len(action_prefix) :]
            try:
                result = perform_dashboard_action(config, action, payload)
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, result)
            return

        self._send_json(404, {"ok": False, "error": "Unknown dashboard endpoint."})

    def _handle_ui_login(self, config) -> None:
        if not config.dashboard_admin_password:
            self._send_json(503, {"ok": False, "error": "DASHBOARD_ADMIN_PASSWORD is not configured."})
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            payload = parse_json_body(raw_body) if raw_body else {}
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        password = ""
        if isinstance(payload, dict):
            password = str(payload.get("password") or "")
        if password != config.dashboard_admin_password:
            self._send_json(401, {"ok": False, "error": "Feil passord."})
            return
        self._send_json(
            200,
            {"ok": True},
            headers={"Set-Cookie": dashboard_session_cookie(config.dashboard_admin_password)},
        )

    def _require_ui_auth(self, config) -> bool:
        if not config.dashboard_admin_password:
            self._send_json(503, {"ok": False, "error": "Dashboard auth is not configured."})
            return False
        authenticated = verify_dashboard_session(
            self.headers.get("Cookie"),
            config.dashboard_admin_password,
        )
        if authenticated:
            return True
        self._send_json(401, {"ok": False, "error": "Unauthorized"})
        return False

    def _handle_finnhub_post(self) -> None:
        received_at = datetime.now(timezone.utc)
        expected_secret = os.getenv("FINNHUB_WEBHOOK_SECRET")
        received_secret = self.headers.get("X-Finnhub-Secret")
        if not verify_finnhub_secret(received_secret, expected_secret):
            self._send_json(401, {"ok": False, "error": "Invalid webhook secret."})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        try:
            payload = parse_json_body(raw_body)
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        normalized_events = normalize_finnhub_webhook(payload, received_at=received_at)
        for event in normalized_events:
            print(
                json.dumps(
                    {
                        "event": VERCEL_STRUCTURED_EVENT_LOG,
                        "received_at": received_at.isoformat(),
                        "record": structured_event_to_record(event),
                    },
                    ensure_ascii=True,
                )
            )

        summary = summarize_payload(payload)
        print(
            json.dumps(
                {
                    "event": "finnhub_webhook_received",
                    "received_at": received_at.isoformat(),
                    "summary": summary,
                    "normalized_event_count": len(normalized_events),
                },
                ensure_ascii=True,
            )
        )
        if normalized_events:
            try:
                summary = run_serverless_news_shock(
                    load_config(),
                    triggering_events=tuple(normalized_events),
                )
                print(
                    json.dumps(
                        {
                            "event": "finnhub_webhook_trade_run",
                            "received_at": received_at.isoformat(),
                            "summary": summary,
                        },
                        ensure_ascii=True,
                    )
                )
            except Exception as exc:  # pragma: no cover - runtime integration safety
                print(
                    json.dumps(
                        {
                            "event": "finnhub_webhook_trade_error",
                            "received_at": received_at.isoformat(),
                            "error": str(exc),
                        },
                        ensure_ascii=True,
                    )
                )
        self.send_response(204)
        self.end_headers()

    def _handle_x_webhook_get(self) -> None:
        config = load_config()
        if not config.x_consumer_secret:
            self._send_json(503, {"ok": False, "error": "X_CONSUMER_SECRET mangler."})
            return
        parsed = urlparse(self.path)
        crc_token = parse_qs(parsed.query).get("crc_token", [None])[0]
        if not crc_token:
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "x-webhook",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            return
        self._send_json(
            200,
            {"response_token": build_x_crc_response_token(crc_token, config.x_consumer_secret)},
        )

    def _handle_x_webhook_post(self) -> None:
        config = load_config()
        received_at = datetime.now(timezone.utc)
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        signature = self.headers.get("x-twitter-webhooks-signature")
        if not verify_x_webhook_signature(raw_body, signature, config.x_consumer_secret):
            self._send_json(401, {"ok": False, "error": "Invalid X webhook signature."})
            return
        try:
            payload = parse_json_body(raw_body)
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        normalized_events = normalize_x_webhook(payload, received_at=received_at)
        for event in normalized_events:
            print(
                json.dumps(
                    {
                        "event": VERCEL_STRUCTURED_EVENT_LOG,
                        "received_at": received_at.isoformat(),
                        "record": structured_event_to_record(event),
                    },
                    ensure_ascii=True,
                )
            )
        print(
            json.dumps(
                {
                    "event": "x_webhook_received",
                    "received_at": received_at.isoformat(),
                    "summary": summarize_payload(payload),
                    "normalized_event_count": len(normalized_events),
                },
                ensure_ascii=True,
            )
        )
        if normalized_events:
            try:
                summary = run_serverless_news_shock(
                    load_config(),
                    triggering_events=tuple(normalized_events),
                )
                print(
                    json.dumps(
                        {
                            "event": "x_webhook_trade_run",
                            "received_at": received_at.isoformat(),
                            "summary": summary,
                        },
                        ensure_ascii=True,
                    )
                )
            except Exception as exc:  # pragma: no cover - runtime integration safety
                print(
                    json.dumps(
                        {
                            "event": "x_webhook_trade_error",
                            "received_at": received_at.isoformat(),
                            "error": str(exc),
                        },
                        ensure_ascii=True,
                    )
                )
        self._send_json(200, {"ok": True, "received": len(normalized_events)})

    def _handle_telegram_post(self) -> None:
        config = load_config()
        received_at = datetime.now(timezone.utc)
        expected_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
        received_secret = self.headers.get(TELEGRAM_SECRET_HEADER)
        if not verify_telegram_secret(received_secret, expected_secret):
            self._send_json(401, {"ok": False, "error": "Invalid Telegram webhook secret."})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        try:
            payload = parse_json_body(raw_body)
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        incoming_message = extract_telegram_message(payload)
        if incoming_message is None:
            self._send_json(
                200,
                {
                    "ok": True,
                    "ignored": True,
                    "reason": "No supported text message in update.",
                },
            )
            return

        model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
        snapshot = self._build_telegram_snapshot(config, incoming_message.chat_id)
        responder = OpenAIChatResponder(
            api_key=os.getenv("OPENAI_API_KEY") or None,
            model=model,
        )
        reply_text = build_local_command_response(
            incoming_message,
            ai_enabled=responder.enabled,
            model=model,
            snapshot=snapshot,
        )
        if reply_text is None:
            action_request = extract_action_request(incoming_message, snapshot=snapshot)
            if action_request is not None:
                try:
                    result = perform_dashboard_action(config, action_request.action, action_request.payload)
                    reply_text = format_action_response(action_request, result)
                except Exception as exc:  # pragma: no cover - defensive fallback for network/runtime errors
                    reply_text = f"Jeg klarte ikke å kjøre kommandoen: <code>{escape(str(exc))}</code>"
        if reply_text is None:
            try:
                reply_text = responder.generate_reply(
                    incoming_message,
                    runtime_context=build_ai_runtime_context(snapshot),
                )
            except Exception as exc:  # pragma: no cover - defensive fallback for network/runtime errors
                reply_text = (
                    "Jeg fikk ikke sendt meldingen videre til AI akkurat nå. "
                    "Prøv igjen snart."
                )
                print(
                    json.dumps(
                        {
                            "event": "telegram_ai_error",
                            "received_at": received_at.isoformat(),
                            "error": str(exc),
                        },
                        ensure_ascii=True,
                    )
                )

        bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or None
        if bot_token:
            try:
                reply_to_telegram_message(
                    bot_token=bot_token,
                    incoming_message=incoming_message,
                    text=reply_text,
                )
            except Exception as exc:  # pragma: no cover - defensive fallback for network/runtime errors
                print(
                    json.dumps(
                        {
                            "event": "telegram_reply_error",
                            "received_at": received_at.isoformat(),
                            "error": str(exc),
                            "chat_id": incoming_message.chat_id,
                        },
                        ensure_ascii=True,
                    )
                )

        print(
            json.dumps(
                {
                    "event": "telegram_message_received",
                    "received_at": received_at.isoformat(),
                    "chat_id": incoming_message.chat_id,
                    "message_id": incoming_message.message_id,
                    "text_preview": incoming_message.text[:120],
                    "summary": summarize_payload(payload),
                    "authorized": snapshot.authorized,
                    "ai_enabled": responder.enabled,
                    "model": model,
                },
                ensure_ascii=True,
            )
        )
        self._send_json(200, {"ok": True})

    def _build_telegram_snapshot(self, config, chat_id: str) -> TelegramRuntimeSnapshot:
        authorized = is_authorized_telegram_chat(config.telegram_chat_id, chat_id)
        dashboard_url = self._dashboard_url()
        if not authorized:
            return TelegramRuntimeSnapshot(authorized=False, dashboard_url=dashboard_url)
        try:
            state_payload = build_dashboard_state_payload(config)
            state_store = build_state_store(config)
            positions = tuple(build_positions_payload(config))
            orders = tuple(state_store.list_recent_orders(limit=8))
            signals = tuple(state_store.list_recent_signals(limit=8))
            events = tuple(state_store.list_recent_events(limit=8))
            settings = tuple(build_settings_payload(config))
        except Exception:
            return TelegramRuntimeSnapshot(authorized=True, dashboard_url=dashboard_url)
        return TelegramRuntimeSnapshot(
            authorized=True,
            dashboard_url=dashboard_url,
            account=state_payload.get("account"),
            control=state_payload.get("control"),
            heartbeat=state_payload.get("heartbeat"),
            positions=positions,
            orders=orders,
            signals=signals,
            events=events,
            settings=settings,
        )

    def _dashboard_url(self) -> str | None:
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host")
        if not host:
            return None
        proto = self.headers.get("X-Forwarded-Proto") or "https"
        return f"{proto}://{host}/dashboard"

    def _handle_cron_news_shock(self) -> None:
        self._handle_cron_request(run_serverless_news_shock)

    def _handle_cron_earnings_scan(self) -> None:
        self._handle_cron_request(run_serverless_earnings_scan)

    def _handle_cron_earnings_run(self) -> None:
        self._handle_cron_request(run_serverless_earnings_once)

    def _handle_cron_request(self, runner) -> None:
        expected_secret = os.getenv("CRON_SECRET")
        received_authorization = self.headers.get("Authorization")
        if not verify_cron_secret(received_authorization, expected_secret):
            self._send_json(401, {"ok": False, "error": "Invalid cron secret."})
            return
        started_at = datetime.now(timezone.utc)
        try:
            summary = runner(load_config())
        except Exception as exc:  # pragma: no cover - runtime integration safety
            self._send_json(
                500,
                {
                    "ok": False,
                    "error": str(exc),
                    "timestamp": started_at.isoformat(),
                },
            )
            return
        self._send_json(
            200,
            {
                "ok": True,
                "timestamp": started_at.isoformat(),
                "summary": summary,
            },
        )

    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, status_code: int, payload: dict, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status_code: int, body: str, headers: dict[str, str] | None = None) -> None:
        raw_body = body.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw_body)))
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw_body)
