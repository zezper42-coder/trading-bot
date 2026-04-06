from trading_bot.config import load_config
from trading_bot.dashboard import (
    build_settings_payload,
    dashboard_logout_cookie,
    dashboard_session_cookie,
    render_dashboard_page,
    verify_dashboard_session,
)


def test_dashboard_session_cookie_roundtrip() -> None:
    cookie_header = dashboard_session_cookie("secret-pass")

    assert verify_dashboard_session(cookie_header, "secret-pass") is True
    assert verify_dashboard_session(cookie_header, "wrong-pass") is False


def test_dashboard_logout_cookie_clears_session() -> None:
    assert "Max-Age=0" in dashboard_logout_cookie()


def test_render_dashboard_page_shows_login_when_not_authenticated(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_ADMIN_PASSWORD", "secret-pass")
    html = render_dashboard_page(load_config(), authenticated=False)

    assert "Åpne dashboard" in html
    assert "Trading Control" in html


def test_build_settings_payload_includes_oil_policy(monkeypatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    settings = build_settings_payload(load_config())

    themes = {item["theme"] for item in settings}
    assert "oil_policy" in themes
    assert "earnings_surprise" in themes
