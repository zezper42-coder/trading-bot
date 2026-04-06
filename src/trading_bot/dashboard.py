from __future__ import annotations

import hashlib
import hmac
import json
from http import cookies
from typing import Any

from trading_bot.config import BotConfig
from trading_bot.domain import BotControlState, StrategySetting, canonical_symbol
from trading_bot.serverless import run_serverless_earnings_scan, run_serverless_news_shock
from trading_bot.state_store import build_state_store

SESSION_COOKIE_NAME = "trading_bot_dashboard_session"
THEME_ORDER = ("btc_news", "tsla_news", "oil_policy", "earnings_surprise")


def verify_dashboard_session(cookie_header: str | None, password: str | None) -> bool:
    if not cookie_header or not password:
        return False
    jar = cookies.SimpleCookie()
    jar.load(cookie_header)
    morsel = jar.get(SESSION_COOKIE_NAME)
    if morsel is None:
        return False
    return hmac.compare_digest(morsel.value, _session_token(password))


def dashboard_session_cookie(password: str) -> str:
    return (
        f"{SESSION_COOKIE_NAME}={_session_token(password)}; "
        "Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=604800"
    )


def dashboard_logout_cookie() -> str:
    return (
        f"{SESSION_COOKIE_NAME}=; Path=/; HttpOnly; Secure; SameSite=Lax; "
        "Expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0"
    )


def build_dashboard_state_payload(config: BotConfig) -> dict[str, Any]:
    broker = None
    account_payload: dict[str, Any] | None = None
    positions_count = 0
    from trading_bot.cli import build_broker

    state_store = build_state_store(config)
    control_state = state_store.get_control_state()
    positions = []
    try:
        broker = build_broker(config)
        account = broker.get_account()
        live_positions = broker.get_all_positions()
        positions_count = sum(1 for position in live_positions.values() if position.qty != 0)
        account_payload = {
            "equity": account.equity,
            "cash": account.cash,
            "buying_power": account.buying_power,
            "open_position_count": positions_count,
        }
        positions = build_positions_payload(config, broker=broker)
    except Exception as exc:
        account_payload = {
            "error": str(exc),
        }

    heartbeat = state_store.latest_heartbeat()
    return {
        "control": {
            "bot_enabled": control_state.bot_enabled,
            "dry_run_override": control_state.dry_run_override,
            "emergency_stop_active": control_state.emergency_stop_active,
            "updated_at": control_state.updated_at.isoformat() if control_state.updated_at else None,
        },
        "account": account_payload,
        "heartbeat": heartbeat,
        "positions_count": positions_count,
        "positions_preview": positions[:6],
        "supabase_configured": bool(config.supabase_url and config.supabase_service_role_key),
    }


def build_dashboard_stream_payload(config: BotConfig) -> dict[str, Any]:
    state_store = build_state_store(config)
    return {
        "state": build_dashboard_state_payload(config),
        "positions": {"ok": True, "rows": build_positions_payload(config)},
        "signals": {"ok": True, "rows": state_store.list_recent_signals()},
        "orders": {"ok": True, "rows": state_store.list_recent_orders()},
        "events": {"ok": True, "rows": state_store.list_recent_events()},
        "settings": {"ok": True, "rows": build_settings_payload(config)},
    }


def build_positions_payload(config: BotConfig, *, broker=None) -> list[dict[str, Any]]:
    from trading_bot.cli import build_broker

    broker = broker or build_broker(config)
    live_positions = broker.get_all_positions()
    state_store = build_state_store(config)
    snapshots = {
        canonical_symbol(row["symbol"]): row
        for row in state_store.list_position_snapshots()
        if row.get("symbol")
    }
    rows: list[dict[str, Any]] = []
    for symbol, position in sorted(live_positions.items()):
        snapshot = snapshots.get(symbol, {})
        rows.append(
            {
                "symbol": position.symbol,
                "qty": position.qty,
                "market_value": position.market_value,
                "avg_entry_price": position.avg_entry_price,
                "stop_price": snapshot.get("stop_price"),
                "trailing_stop_price": snapshot.get("trailing_stop_price"),
                "event_id": snapshot.get("event_id"),
                "theme": snapshot.get("theme"),
                "updated_at": snapshot.get("updated_at"),
            }
        )
    return rows


def build_settings_payload(config: BotConfig) -> list[dict[str, Any]]:
    state_store = build_state_store(config)
    settings = state_store.get_strategy_settings()
    ordered_settings: list[dict[str, Any]] = []
    for theme in THEME_ORDER:
        setting = settings.get(theme) or StrategySetting(theme=theme)
        ordered_settings.append(_setting_to_dict(setting))
    for theme, setting in settings.items():
        if theme in THEME_ORDER:
            continue
        ordered_settings.append(_setting_to_dict(setting))
    return ordered_settings


def update_settings_payload(config: BotConfig, payload: dict[str, Any]) -> list[dict[str, Any]]:
    state_store = build_state_store(config)
    raw_settings = payload.get("settings") if isinstance(payload, dict) else None
    settings_items: list[dict[str, Any]] = []
    if isinstance(raw_settings, list):
        settings_items = [item for item in raw_settings if isinstance(item, dict)]
    elif isinstance(payload, dict) and payload.get("theme"):
        settings_items = [payload]

    updated: list[dict[str, Any]] = []
    for item in settings_items:
        theme = str(item.get("theme") or "").strip()
        if not theme:
            continue
        clean_payload = {
            "enabled": bool(item.get("enabled", True)),
            "min_surprise": _nullable_float(item.get("min_surprise")),
            "min_confidence": _nullable_float(item.get("min_confidence")),
            "min_sentiment": _nullable_float(item.get("min_sentiment")),
            "min_source_count": _nullable_int(item.get("min_source_count")),
            "confirmation_bars": _nullable_int(item.get("confirmation_bars")),
            "volume_multiplier": _nullable_float(item.get("volume_multiplier")),
            "max_event_age_seconds": _nullable_int(item.get("max_event_age_seconds")),
            "risk_per_trade": _nullable_float(item.get("risk_per_trade")),
            "risk_multiplier_min": _nullable_float(item.get("risk_multiplier_min")),
            "risk_multiplier_max": _nullable_float(item.get("risk_multiplier_max")),
            "min_trade_score": _nullable_float(item.get("min_trade_score")),
        }
        updated_setting = state_store.upsert_strategy_setting(theme, clean_payload)
        updated.append(_setting_to_dict(updated_setting))
    return updated


def perform_dashboard_action(config: BotConfig, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from trading_bot.cli import build_broker

    payload = payload or {}
    state_store = build_state_store(config)
    control_state = state_store.get_control_state()

    if action == "stop":
        new_state = state_store.set_control_state(
            bot_enabled=False,
            dry_run_override=control_state.dry_run_override,
            emergency_stop_active=control_state.emergency_stop_active,
        )
        return {"ok": True, "control": _control_to_dict(new_state)}

    if action == "resume":
        new_state = state_store.set_control_state(
            bot_enabled=True,
            dry_run_override=control_state.dry_run_override,
            emergency_stop_active=False,
        )
        return {"ok": True, "control": _control_to_dict(new_state)}

    if action == "dry-run":
        dry_run_value = bool(payload.get("dry_run", True))
        new_state = state_store.set_control_state(
            bot_enabled=control_state.bot_enabled,
            dry_run_override=dry_run_value,
            emergency_stop_active=control_state.emergency_stop_active,
        )
        return {"ok": True, "control": _control_to_dict(new_state)}

    broker = build_broker(config)
    if action == "cancel-open-orders":
        orders = broker.cancel_all_orders()
        return {"ok": True, "cancelled": orders}

    if action == "emergency-liquidate":
        cancelled = broker.cancel_all_orders()
        closed = broker.close_all_positions()
        new_state = state_store.set_control_state(
            bot_enabled=False,
            dry_run_override=control_state.dry_run_override,
            emergency_stop_active=True,
        )
        return {
            "ok": True,
            "cancelled": cancelled,
            "closed": closed,
            "control": _control_to_dict(new_state),
        }

    if action == "run-news-eval":
        return {"ok": True, "summary": run_serverless_news_shock(config)}

    if action == "run-earnings-scan":
        return {"ok": True, "summary": run_serverless_earnings_scan(config)}

    raise ValueError(f"Unsupported dashboard action: {action}")


def render_dashboard_page(config: BotConfig, *, authenticated: bool) -> str:
    if not authenticated:
        return _render_login_page()
    return _render_app_page()


def _render_login_page() -> str:
    return """<!doctype html>
<html lang="no">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Trading Control</title>
  <style>
    :root {
      --bg: #f5f1e8;
      --fg: #0f172a;
      --muted: #5b6470;
      --line: rgba(15, 23, 42, 0.14);
      --accent: #0f766e;
      --accent-strong: #115e59;
      --panel: rgba(255,255,255,0.68);
      --shadow: 0 24px 60px rgba(15, 23, 42, 0.10);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.10), transparent 34%),
        linear-gradient(180deg, #fbf8f1 0%, var(--bg) 60%, #ede5d8 100%);
      color: var(--fg);
    }
    .sheet {
      width: min(520px, calc(100vw - 32px));
      padding: 28px 28px 24px;
      background: var(--panel);
      backdrop-filter: blur(18px);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
    }
    h1 {
      margin: 0 0 8px;
      font-size: clamp(2rem, 6vw, 3.2rem);
      line-height: 0.95;
      letter-spacing: -0.04em;
    }
    p {
      margin: 0 0 24px;
      color: var(--muted);
      font-size: 0.98rem;
      max-width: 32ch;
    }
    form {
      display: grid;
      gap: 14px;
    }
    input {
      width: 100%;
      padding: 14px 16px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.78);
      font-size: 1rem;
      color: var(--fg);
      outline: none;
    }
    input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 4px rgba(15,118,110,0.12);
    }
    button {
      border: none;
      background: linear-gradient(135deg, var(--accent) 0%, var(--accent-strong) 100%);
      color: white;
      padding: 14px 18px;
      font-weight: 700;
      cursor: pointer;
    }
    .error {
      min-height: 1.2rem;
      color: #b42318;
      font-size: 0.92rem;
    }
  </style>
</head>
<body>
  <main class="sheet">
    <h1>Trading Control</h1>
    <p>Logg inn for å styre nyhetsmotoren, overvåke posisjoner og sende manuelle kontrollkommandoer.</p>
    <form id="login-form">
      <input id="password" type="password" placeholder="Dashboard-passord" autocomplete="current-password" />
      <button type="submit">Åpne dashboard</button>
      <div class="error" id="error"></div>
    </form>
  </main>
  <script>
    document.getElementById("login-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const password = document.getElementById("password").value;
      const error = document.getElementById("error");
      error.textContent = "";
      const response = await fetch("/api/ui/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password })
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        error.textContent = data.error || "Login feilet.";
        return;
      }
      window.location.reload();
    });
  </script>
</body>
</html>"""


def _render_app_page() -> str:
    return """<!doctype html>
<html lang="no">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Trading Control</title>
  <style>
    :root {
      --bg: #f4efe5;
      --surface: rgba(255,255,255,0.72);
      --line: rgba(15,23,42,0.10);
      --ink: #0f172a;
      --muted: #5b6470;
      --accent: #0f766e;
      --danger: #b42318;
      --warn: #9a6700;
      --good: #166534;
      --shadow: 0 12px 48px rgba(15,23,42,0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top right, rgba(15,118,110,0.12), transparent 28%),
        linear-gradient(180deg, #fcf9f3 0%, #f4efe5 55%, #ece2d2 100%);
    }
    .page {
      padding: 22px;
      display: grid;
      gap: 20px;
    }
    .masthead {
      display: grid;
      grid-template-columns: 1.25fr 1fr;
      gap: 24px;
      align-items: end;
      min-height: 240px;
      padding: 26px 28px;
      background:
        linear-gradient(135deg, rgba(255,255,255,0.80), rgba(255,255,255,0.52)),
        linear-gradient(120deg, rgba(15,118,110,0.16), rgba(15,23,42,0.00));
      border-bottom: 1px solid var(--line);
      box-shadow: var(--shadow);
    }
    .eyebrow {
      margin: 0 0 14px;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      font-size: 0.72rem;
      color: var(--muted);
    }
    h1 {
      margin: 0;
      font-size: clamp(2.4rem, 5vw, 5.2rem);
      line-height: 0.92;
      letter-spacing: -0.05em;
      max-width: 10ch;
    }
    .lead {
      margin: 14px 0 0;
      color: var(--muted);
      max-width: 48ch;
      font-size: 1rem;
    }
    .hero-metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      align-self: stretch;
    }
    .metric {
      display: grid;
      align-content: end;
      padding: 18px 0;
      border-top: 1px solid var(--line);
    }
    .metric label {
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.14em;
      color: var(--muted);
    }
    .metric strong {
      font-size: 2rem;
      letter-spacing: -0.04em;
    }
    .toolbar, .workspace {
      display: grid;
      gap: 18px;
    }
    .toolbar {
      grid-template-columns: repeat(6, minmax(0, 1fr));
      align-items: start;
    }
    .button {
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.65);
      padding: 14px 14px 12px;
      cursor: pointer;
      text-align: left;
      min-height: 74px;
      transition: transform 120ms ease, border-color 120ms ease;
    }
    .button:hover { transform: translateY(-1px); border-color: rgba(15,118,110,0.45); }
    .button strong {
      display: block;
      margin-bottom: 6px;
      font-size: 0.92rem;
    }
    .button span { color: var(--muted); font-size: 0.85rem; }
    .button.danger { border-color: rgba(180,35,24,0.24); }
    .workspace {
      grid-template-columns: 1.3fr 1fr;
      align-items: start;
    }
    section {
      background: var(--surface);
      border-top: 1px solid var(--line);
      box-shadow: var(--shadow);
      padding: 18px 18px 20px;
    }
    .section-header {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: end;
      margin-bottom: 12px;
    }
    .section-header h2 {
      margin: 0;
      font-size: 1.2rem;
      letter-spacing: -0.03em;
    }
    .section-header p {
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 0.9rem;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92rem;
    }
    th, td {
      padding: 10px 0;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    th {
      text-align: left;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--muted);
      padding-right: 10px;
    }
    td { padding-right: 10px; }
    code, .mono {
      font-family: "IBM Plex Mono", "SFMono-Regular", ui-monospace, monospace;
      font-size: 0.85rem;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 4px 10px;
      border: 1px solid var(--line);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.10em;
      color: var(--muted);
    }
    .status-good { color: var(--good); }
    .status-danger { color: var(--danger); }
    .status-warn { color: var(--warn); }
    .split {
      display: grid;
      grid-template-columns: 1fr;
      gap: 18px;
    }
    .settings-grid {
      display: grid;
      gap: 12px;
    }
    .settings-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: flex-start;
      border-bottom: 1px solid var(--line);
      padding-bottom: 12px;
    }
    .settings-row > div:first-child {
      flex: 1 1 240px;
      min-width: 240px;
      padding-right: 8px;
    }
    .settings-row > div:not(:first-child) {
      flex: 1 1 130px;
      min-width: 130px;
    }
    .settings-row label {
      font-size: 0.78rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.10em;
      display: block;
      margin-bottom: 4px;
    }
    .settings-row .theme-name {
      font-size: 0.92rem;
      font-weight: 700;
      text-transform: none;
      letter-spacing: 0;
      color: var(--ink);
    }
    .settings-row input {
      width: 100%;
      padding: 10px 11px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.70);
      color: var(--ink);
    }
    .settings-row input[type="checkbox"] {
      width: auto;
      justify-self: start;
      transform: scale(1.1);
    }
    .event-list, .signal-list {
      display: grid;
      gap: 14px;
    }
    .event-item, .signal-item {
      padding-bottom: 14px;
      border-bottom: 1px solid var(--line);
    }
    .event-item:last-child, .signal-item:last-child { border-bottom: none; }
    .headline {
      font-size: 1rem;
      line-height: 1.35;
      margin: 0 0 6px;
    }
    .meta {
      color: var(--muted);
      font-size: 0.82rem;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }
    .footer-actions {
      display: flex;
      gap: 12px;
      margin-top: 12px;
      flex-wrap: wrap;
    }
    .tiny-button {
      padding: 10px 14px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.78);
      cursor: pointer;
    }
    @media (max-width: 1100px) {
      .masthead, .workspace, .toolbar {
        grid-template-columns: 1fr;
      }
      .settings-row > div:first-child { min-width: 100%; }
      .settings-row > div:not(:first-child) { min-width: 45%; }
    }
  </style>
</head>
<body>
  <div class="page">
    <header class="masthead">
      <div>
        <p class="eyebrow">Global News Control Plane</p>
        <h1>Trading Control Room</h1>
        <p class="lead">Styr BTC, TSLA, earnings og Trump/olje-proxyer fra ett kontrollpanel. Hele flaten oppdateres via polling og lar deg stoppe boten, justere terskler og likvidere alt umiddelbart.</p>
      </div>
      <div class="hero-metrics" id="hero-metrics"></div>
    </header>

    <div class="toolbar">
      <button class="button" onclick="runControl('stop')"><strong>Stopp bot</strong><span>Blokker nye entries, behold exits.</span></button>
      <button class="button" onclick="runControl('resume')"><strong>Resume</strong><span>Aktiver nye handler igjen.</span></button>
      <button class="button" onclick="toggleDryRun()"><strong>Dry run</strong><span>Slå execution av eller på persistent.</span></button>
      <button class="button danger" onclick="runControl('emergency-liquidate')"><strong>Nødselg</strong><span>Kanseller åpne ordre og lukk alle posisjoner.</span></button>
      <button class="button" onclick="runControl('cancel-open-orders')"><strong>Kanseller pending</strong><span>Rydd opp i åpne Alpaca-ordrer.</span></button>
      <button class="button" onclick="runAction('run-earnings-scan')"><strong>Kjør scan nå</strong><span>Bygg ny earnings-watchlist med én gang.</span></button>
    </div>

    <div class="workspace">
      <div class="split">
        <section>
          <div class="section-header">
            <div>
              <h2>Oversikt</h2>
              <p>Konto, kontrollstate og siste heartbeat.</p>
            </div>
            <div class="pill mono" id="bot-status">Laster…</div>
          </div>
          <div id="overview-grid"></div>
          <div class="footer-actions">
            <button class="tiny-button" onclick="runAction('run-news-eval')">Kjør news eval nå</button>
            <button class="tiny-button" onclick="logout()">Logg ut</button>
          </div>
        </section>

        <section>
          <div class="section-header">
            <div>
              <h2>Åpne Posisjoner</h2>
              <p>Live broker-state med stops og koblet event-id.</p>
            </div>
          </div>
          <div id="positions-table"></div>
        </section>

        <section>
          <div class="section-header">
            <div>
              <h2>Signaler og Ordre</h2>
              <p>Siste vurderinger og execution-beslutninger.</p>
            </div>
          </div>
          <div id="signals-list" class="signal-list"></div>
          <div id="orders-table"></div>
        </section>
      </div>

      <div class="split">
        <section>
          <div class="section-header">
            <div>
              <h2>Risk &amp; Settings</h2>
              <p>Juster hvor god nyheten må være, per tema.</p>
            </div>
            <button class="tiny-button" onclick="saveSettings()">Lagre settings</button>
          </div>
          <div id="settings-grid" class="settings-grid"></div>
        </section>

        <section>
          <div class="section-header">
            <div>
              <h2>Live Nyheter</h2>
              <p>Headline, tema, score og vurdering før trade.</p>
            </div>
          </div>
          <div id="events-list" class="event-list"></div>
        </section>
      </div>
    </div>
  </div>
  <script>
    const state = {
      control: null,
      settings: []
    };

    function formatNumber(value, digits = 2) {
      if (value === null || value === undefined || value === "") return "—";
      const number = Number(value);
      if (!Number.isFinite(number)) return "—";
      return number.toLocaleString("no-NO", { maximumFractionDigits: digits, minimumFractionDigits: digits });
    }

    function formatMaybe(value) {
      return value === null || value === undefined || value === "" ? "—" : value;
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        credentials: "same-origin",
        ...options
      });
      const contentType = response.headers.get("content-type") || "";
      const data = contentType.includes("application/json") ? await response.json() : await response.text();
      if (!response.ok) {
        throw new Error(data.error || data.message || response.statusText || "Request failed");
      }
      return data;
    }

    function renderHeroMetrics(payload) {
      const metrics = [];
      const account = payload.account || {};
      metrics.push(["Equity", account.equity ? formatNumber(account.equity, 2) : "—"]);
      metrics.push(["Cash", account.cash ? formatNumber(account.cash, 2) : "—"]);
      metrics.push(["Buying Power", account.buying_power ? formatNumber(account.buying_power, 2) : "—"]);
      metrics.push(["Open Positions", String(payload.positions_count || 0)]);
      document.getElementById("hero-metrics").innerHTML = metrics.map(([label, value]) => `
        <div class="metric">
          <label>${label}</label>
          <strong>${value}</strong>
        </div>
      `).join("");
    }

    function renderOverview(payload) {
      state.control = payload.control;
      const control = payload.control || {};
      const heartbeat = payload.heartbeat || {};
      const botStatus = document.getElementById("bot-status");
      botStatus.textContent = control.bot_enabled ? "Bot enabled" : "Bot paused";
      botStatus.className = `pill mono ${control.bot_enabled ? "status-good" : "status-danger"}`;
      document.getElementById("overview-grid").innerHTML = `
        <table>
          <tbody>
            <tr><th>Bot enabled</th><td>${String(control.bot_enabled)}</td></tr>
            <tr><th>Dry run override</th><td>${formatMaybe(control.dry_run_override)}</td></tr>
            <tr><th>Emergency stop</th><td>${String(control.emergency_stop_active)}</td></tr>
            <tr><th>Heartbeat status</th><td>${formatMaybe(heartbeat.status)}</td></tr>
            <tr><th>Heartbeat strategy</th><td>${formatMaybe(heartbeat.strategy)}</td></tr>
            <tr><th>Sist heartbeat</th><td class="mono">${formatMaybe(heartbeat.updated_at)}</td></tr>
            <tr><th>Supabase</th><td>${payload.supabase_configured ? "configured" : "not configured"}</td></tr>
          </tbody>
        </table>
      `;
      renderHeroMetrics(payload);
    }

    function renderPositions(rows) {
      if (!rows.length) {
        document.getElementById("positions-table").innerHTML = "<p class='meta'>Ingen åpne posisjoner.</p>";
        return;
      }
      document.getElementById("positions-table").innerHTML = `
        <table>
          <thead><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Value</th><th>Stop</th><th>Theme</th><th>Event</th></tr></thead>
          <tbody>
            ${rows.map((row) => `
              <tr>
                <td><strong>${row.symbol}</strong></td>
                <td class="mono">${formatNumber(row.qty, 4)}</td>
                <td class="mono">${formatNumber(row.avg_entry_price, 4)}</td>
                <td class="mono">${formatNumber(row.market_value, 2)}</td>
                <td class="mono">${formatMaybe(row.trailing_stop_price ?? row.stop_price)}</td>
                <td>${formatMaybe(row.theme)}</td>
                <td class="mono">${formatMaybe(row.event_id)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
    }

    function renderSignals(rows) {
      document.getElementById("signals-list").innerHTML = rows.slice(0, 8).map((row) => `
        <article class="signal-item">
          <div class="meta"><span>${row.symbol}</span><span>${row.action}</span><span>${formatMaybe(row.theme)}</span><span class="mono">${formatMaybe(row.timestamp)}</span></div>
          <p class="headline">${row.reason || "—"}</p>
          <div class="meta">
            <span>trade_score ${formatMaybe(row.trade_score)}</span>
            <span>surprise ${formatMaybe(row.surprise_score)}</span>
            <span>confidence ${formatMaybe(row.confidence_score)}</span>
            <span>risk ${formatMaybe(row.risk_per_trade_override)}</span>
          </div>
        </article>
      `).join("") || "<p class='meta'>Ingen signaler ennå.</p>";
    }

    function renderOrders(rows) {
      if (!rows.length) {
        document.getElementById("orders-table").innerHTML = "<p class='meta'>Ingen ordre logget ennå.</p>";
        return;
      }
      document.getElementById("orders-table").innerHTML = `
        <table>
          <thead><tr><th>Ordre</th><th>Side</th><th>Qty</th><th>Pris</th><th>Theme</th><th>Status</th></tr></thead>
          <tbody>
            ${rows.slice(0, 12).map((row) => `
              <tr>
                <td class="mono">${row.order_id}</td>
                <td>${row.side}</td>
                <td class="mono">${formatMaybe(row.qty)}</td>
                <td class="mono">${formatMaybe(row.price)}</td>
                <td>${formatMaybe(row.theme)}</td>
                <td>${formatMaybe(row.status)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
    }

    function renderEvents(rows) {
      document.getElementById("events-list").innerHTML = rows.slice(0, 12).map((row) => `
        <article class="event-item">
          <div class="meta">
            <span>${formatMaybe(row.theme)}</span>
            <span>${formatMaybe(row.source)}</span>
            <span class="mono">${formatMaybe(row.published_at)}</span>
          </div>
          <p class="headline">${row.headline}</p>
          <div class="meta">
            <span>scope ${Array.isArray(row.instrument_scope) ? row.instrument_scope.join(", ") : "—"}</span>
            <span>trade_score ${formatMaybe(row.trade_score)}</span>
            <span>direction ${formatMaybe(row.direction_score)}</span>
            <span>magnitude ${formatMaybe(row.magnitude_score)}</span>
            <span>unexpected ${formatMaybe(row.unexpectedness_score)}</span>
          </div>
        </article>
      `).join("") || "<p class='meta'>Ingen events i feeden ennå.</p>";
    }

    function renderSettings(rows) {
      state.settings = rows;
      const fields = [
        ["enabled", "På"],
        ["min_surprise", "Min surprise"],
        ["min_confidence", "Min confidence"],
        ["min_sentiment", "Min sentiment"],
        ["min_source_count", "Min kilder"],
        ["confirmation_bars", "Bars"],
        ["volume_multiplier", "Volum"],
        ["max_event_age_seconds", "Max age"],
        ["risk_per_trade", "Risk/trade"],
        ["risk_multiplier_min", "Risk min"],
        ["risk_multiplier_max", "Risk max"],
        ["min_trade_score", "Trade score"]
      ];
      document.getElementById("settings-grid").innerHTML = rows.map((row) => `
        <div class="settings-row" data-theme="${row.theme}">
          <div>
            <div class="theme-name">${row.theme}</div>
            <div class="meta">Oppdatert ${formatMaybe(row.updated_at)}</div>
          </div>
          ${fields.map(([key, label]) => `
            <div>
              <label>${label}</label>
              ${key === "enabled"
                ? `<input type="checkbox" data-field="${key}" ${row[key] ? "checked" : ""} />`
                : `<input type="text" data-field="${key}" value="${row[key] ?? ""}" />`
              }
            </div>
          `).join("")}
        </div>
      `).join("");
    }

    async function refreshAll() {
      const [statePayload, positions, signals, orders, events, settings] = await Promise.all([
        api("/api/ui/state"),
        api("/api/ui/positions"),
        api("/api/ui/signals"),
        api("/api/ui/orders"),
        api("/api/ui/events"),
        api("/api/ui/settings")
      ]);
      renderOverview(statePayload);
      renderPositions(positions.rows || []);
      renderSignals(signals.rows || []);
      renderOrders(orders.rows || []);
      renderEvents(events.rows || []);
      renderSettings(settings.rows || []);
    }

    async function runControl(action, payload = {}) {
      try {
        await api(`/api/ui/control/${action}`, { method: "POST", body: JSON.stringify(payload) });
        await refreshAll();
      } catch (error) {
        alert(error.message);
      }
    }

    async function runAction(action, payload = {}) {
      try {
        await api(`/api/ui/action/${action}`, { method: "POST", body: JSON.stringify(payload) });
        await refreshAll();
      } catch (error) {
        alert(error.message);
      }
    }

    async function toggleDryRun() {
      const current = state.control?.dry_run_override === true;
      await runControl("dry-run", { dry_run: !current });
    }

    async function saveSettings() {
      const rows = [...document.querySelectorAll(".settings-row")].map((row) => {
        const theme = row.dataset.theme;
        const payload = { theme };
        row.querySelectorAll("[data-field]").forEach((input) => {
          const field = input.dataset.field;
          payload[field] = input.type === "checkbox" ? input.checked : input.value;
        });
        return payload;
      });
      try {
        await api("/api/ui/settings", {
          method: "POST",
          body: JSON.stringify({ settings: rows })
        });
        await refreshAll();
      } catch (error) {
        alert(error.message);
      }
    }

    async function logout() {
      await api("/api/ui/logout", { method: "POST", body: JSON.stringify({}) });
      window.location.reload();
    }

    let pollingTimer = null;
    let eventSource = null;

    function applySnapshot(snapshot) {
      if (!snapshot) return;
      if (snapshot.state) renderOverview(snapshot.state);
      if (snapshot.positions) renderPositions(snapshot.positions.rows || []);
      if (snapshot.signals) renderSignals(snapshot.signals.rows || []);
      if (snapshot.orders) renderOrders(snapshot.orders.rows || []);
      if (snapshot.events) renderEvents(snapshot.events.rows || []);
      if (snapshot.settings) renderSettings(snapshot.settings.rows || []);
    }

    function startPollingFallback() {
      if (pollingTimer) return;
      pollingTimer = setInterval(() => refreshAll().catch(() => {}), 5000);
    }

    function stopPollingFallback() {
      if (!pollingTimer) return;
      clearInterval(pollingTimer);
      pollingTimer = null;
    }

    function startLiveStream() {
      if (typeof EventSource === "undefined") {
        startPollingFallback();
        return;
      }
      eventSource = new EventSource("/api/ui/stream", { withCredentials: true });
      eventSource.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data || "{}");
          applySnapshot(parsed.payload);
          stopPollingFallback();
        } catch (_) {}
      };
      eventSource.onerror = () => {
        startPollingFallback();
      };
    }

    refreshAll().then(() => {
      startLiveStream();
    }).catch((error) => {
      alert(error.message);
      startPollingFallback();
    });
  </script>
</body>
</html>"""


def _session_token(password: str) -> str:
    return hmac.new(password.encode("utf-8"), b"trading-bot-dashboard", hashlib.sha256).hexdigest()


def _setting_to_dict(setting: StrategySetting) -> dict[str, Any]:
    return {
        "theme": setting.theme,
        "enabled": setting.enabled,
        "min_surprise": setting.min_surprise,
        "min_confidence": setting.min_confidence,
        "min_sentiment": setting.min_sentiment,
        "min_source_count": setting.min_source_count,
        "confirmation_bars": setting.confirmation_bars,
        "volume_multiplier": setting.volume_multiplier,
        "max_event_age_seconds": setting.max_event_age_seconds,
        "risk_per_trade": setting.risk_per_trade,
        "risk_multiplier_min": setting.risk_multiplier_min,
        "risk_multiplier_max": setting.risk_multiplier_max,
        "min_trade_score": setting.min_trade_score,
        "updated_at": setting.updated_at.isoformat() if setting.updated_at else None,
    }


def _control_to_dict(control_state: BotControlState) -> dict[str, Any]:
    return {
        "bot_enabled": control_state.bot_enabled,
        "dry_run_override": control_state.dry_run_override,
        "emergency_stop_active": control_state.emergency_stop_active,
        "updated_at": control_state.updated_at.isoformat() if control_state.updated_at else None,
    }


def _nullable_float(value: Any) -> float | None:
    if value in {None, "", "null"}:
        return None
    return float(value)


def _nullable_int(value: Any) -> int | None:
    if value in {None, "", "null"}:
        return None
    return int(value)
