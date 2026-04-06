from trading_bot.config import (
    load_config,
    parse_feed_map,
    parse_instruments,
    parse_saxo_instrument_map,
    parse_strategy_kind,
)
from trading_bot.domain import AssetClass, BrokerKind, StrategyKind


def test_parse_instruments_supports_stock_and_crypto() -> None:
    instruments = parse_instruments("TSLA:stock,BTC/USD:crypto")

    assert instruments[0].symbol == "TSLA"
    assert instruments[0].asset_class is AssetClass.STOCK
    assert instruments[1].symbol == "BTC/USD"
    assert instruments[1].asset_class is AssetClass.CRYPTO


def test_parse_strategy_kind_maps_news_shock() -> None:
    assert parse_strategy_kind("news_shock") is StrategyKind.NEWS_SHOCK


def test_parse_strategy_kind_maps_earnings_surprise() -> None:
    assert parse_strategy_kind("earnings_surprise") is StrategyKind.EARNINGS_SURPRISE


def test_load_config_defaults_to_news_shock(monkeypatch) -> None:
    monkeypatch.setenv("BOT_STRATEGY", "news_shock")
    monkeypatch.delenv("STRUCTURED_EVENTS_PATH", raising=False)
    config = load_config()

    assert config.strategy_kind is StrategyKind.NEWS_SHOCK
    assert config.broker_kind is BrokerKind.ALPACA


def test_parse_feed_map_supports_named_rss_urls() -> None:
    mappings = parse_feed_map(
        "sec_press=https://www.sec.gov/news/pressreleases.rss,fed=https://www.federalreserve.gov/feeds/press_monetary.xml"
    )

    assert mappings[0][0] == "sec_press"
    assert mappings[1][1].startswith("https://www.federalreserve.gov/")


def test_parse_saxo_instrument_map_supports_symbol_to_uic_pairs() -> None:
    mappings = parse_saxo_instrument_map("EQNR=1234,ORK=5678")

    assert mappings == (("EQNR", 1234), ("ORK", 5678))


def test_load_config_parses_telegram_settings(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    monkeypatch.setenv("TELEGRAM_DISABLE_NOTIFICATION", "true")
    monkeypatch.setenv("TELEGRAM_MESSAGE_THREAD_ID", "7")

    config = load_config()

    assert config.telegram_bot_token == "telegram-token"
    assert config.telegram_chat_id == "-100123"
    assert config.telegram_disable_notification is True
    assert config.telegram_message_thread_id == 7
