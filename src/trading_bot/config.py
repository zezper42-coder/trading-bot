from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from alpaca.data.enums import DataFeed
from dotenv import load_dotenv

from trading_bot.domain import AssetClass, BrokerKind, Instrument, StrategyKind


@dataclass(frozen=True)
class BotConfig:
    broker_kind: BrokerKind
    alpaca_api_key: str | None
    alpaca_api_secret: str | None
    alpaca_paper: bool
    alpaca_stock_feed: DataFeed
    saxo_access_token: str | None
    saxo_environment: str
    saxo_account_key: str | None
    saxo_default_exchange_id: str
    saxo_client_key: str | None
    saxo_instrument_map: tuple[tuple[str, int], ...]
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    telegram_disable_notification: bool
    telegram_message_thread_id: int | None
    supabase_url: str | None
    supabase_anon_key: str | None
    supabase_service_role_key: str | None
    dashboard_admin_password: str | None
    instruments: tuple[Instrument, ...]
    strategy_kind: StrategyKind
    surprise_provider: str
    finnhub_api_key: str | None
    global_news_enabled: bool
    vercel_webhook_logs_enabled: bool
    vercel_webhook_scope: str | None
    vercel_webhook_environment: str
    vercel_webhook_logs_since_minutes: int
    vercel_webhook_cwd: Path | None
    official_rss_feeds_enabled: bool
    official_rss_feeds: tuple[tuple[str, str], ...]
    sec_tsla_submissions_enabled: bool
    sec_api_user_agent: str
    x_webhook_enabled: bool
    x_consumer_secret: str | None
    x_webhook_url: str | None
    x_filtered_stream_rule: str
    x_filtered_stream_rule_tag: str
    x_stream_enabled: bool
    x_stream_connect_timeout_seconds: int
    x_stream_read_timeout_seconds: int
    x_stream_max_backoff_seconds: int
    x_recent_search_enabled: bool
    x_bearer_token: str | None
    x_recent_search_query: str
    x_recent_search_max_results: int
    short_window: int
    long_window: int
    lookback_bars: int
    loop_interval_seconds: int
    risk_per_trade: float
    cash_buffer: float
    max_open_positions: int
    min_notional_usd: float
    max_daily_loss_pct: float
    news_shock_min_surprise: float
    news_shock_min_confidence: float
    news_shock_min_sentiment: float
    news_shock_min_source_count: int
    news_shock_confirmation_bars: int
    news_shock_volume_multiplier: float
    news_shock_max_event_age_seconds: int
    news_shock_realtime_window_seconds: int
    news_shock_btc_max_hold_minutes: int
    news_shock_stock_flatten_minutes_before_close: int
    news_shock_target_leverage: float
    news_shock_btc_min_surprise: float
    news_shock_btc_min_confidence: float
    news_shock_btc_min_sentiment: float
    news_shock_btc_min_source_count: int
    news_shock_btc_confirmation_bars: int
    news_shock_btc_volume_multiplier: float
    news_shock_btc_momentum_fade_bars: int
    news_shock_btc_momentum_fade_min_profit_pct: float
    news_shock_btc_momentum_fade_from_high_pct: float
    oil_proxy_symbols: tuple[str, ...]
    news_shock_oil_min_trade_score: float
    news_shock_oil_min_confidence: float
    news_shock_oil_confirmation_bars: int
    news_shock_oil_volume_multiplier: float
    news_shock_oil_risk_per_trade: float
    earnings_provider: str
    earnings_lookahead_days: int
    earnings_universe_max_size: int
    earnings_market_cap_min_usd: float
    earnings_market_cap_max_usd: float
    earnings_min_price_usd: float
    earnings_min_avg_dollar_volume_usd: float
    earnings_min_eps_surprise_pct: float
    earnings_min_revenue_surprise_pct: float
    earnings_max_event_age_seconds: int
    earnings_confirmation_bars: int
    earnings_volume_multiplier: float
    earnings_risk_per_trade: float
    earnings_risk_multiplier_min: float
    earnings_risk_multiplier_max: float
    earnings_max_open_positions: int
    earnings_max_daily_loss_pct: float
    earnings_watchlist_limit: int
    earnings_telegram_watchlist_enabled: bool
    earnings_db_path: Path | None
    social_watch_authors: tuple[str, ...]
    social_feed_path: Path | None
    social_lookback_minutes: int
    social_min_sentiment_score: float
    social_min_engagement_score: float
    news_feed_path: Path | None
    news_lookback_minutes: int
    news_buy_surprise_threshold: float
    news_sell_surprise_threshold: float
    news_min_sentiment_score: float
    structured_events_path: Path | None
    trade_log_path: Path | None
    dry_run: bool

    def require_trading_credentials(self) -> None:
        if self.broker_kind is BrokerKind.ALPACA and self.alpaca_api_key and self.alpaca_api_secret:
            return
        if self.broker_kind is BrokerKind.SAXO:
            if any(instrument.asset_class is AssetClass.CRYPTO for instrument in self.instruments):
                raise RuntimeError("SaxoBroker støtter foreløpig bare aksjer i dette prosjektet.")
            if self.saxo_access_token:
                return
            raise RuntimeError("SAXO_ACCESS_TOKEN må være satt for Saxo paper/sim trading.")
        raise RuntimeError("Ukjent broker-konfigurasjon.")

    def require_market_data_credentials(self, asset_class: AssetClass) -> None:
        if self.broker_kind is BrokerKind.SAXO:
            if asset_class is AssetClass.CRYPTO:
                raise RuntimeError("SaxoBroker støtter ikke krypto i dette prosjektet.")
            if self.saxo_access_token:
                return
            raise RuntimeError("SAXO_ACCESS_TOKEN må være satt for Saxo market data.")
        if asset_class is AssetClass.CRYPTO:
            return
        if self.alpaca_api_key and self.alpaca_api_secret:
            return
        raise RuntimeError(
            "Alpaca stock-data krever API-nøkler. Sett ALPACA_API_KEY og ALPACA_API_SECRET."
        )

    def require_news_shock_provider(self) -> None:
        if self.strategy_kind is not StrategyKind.NEWS_SHOCK:
            return
        if self.structured_events_path is not None:
            return
        if self.vercel_webhook_logs_enabled:
            return
        if self.official_rss_feeds_enabled or self.sec_tsla_submissions_enabled:
            return
        if self.surprise_provider == "finnhub" and self.finnhub_api_key:
            return
        raise RuntimeError(
            "news_shock krever STRUCTURED_EVENTS_PATH, Vercel webhook-log-feed, offisielle feeds eller FINNHUB_API_KEY."
        )

    def require_earnings_provider(self) -> None:
        if self.strategy_kind is not StrategyKind.EARNINGS_SURPRISE:
            return
        if self.broker_kind is not BrokerKind.ALPACA:
            raise RuntimeError("earnings_surprise støtter foreløpig bare Alpaca.")
        if self.earnings_provider != "finnhub":
            raise RuntimeError("earnings_surprise støtter foreløpig bare EARNINGS_PROVIDER=finnhub.")
        if not self.finnhub_api_key:
            raise RuntimeError("earnings_surprise krever FINNHUB_API_KEY.")


def load_config() -> BotConfig:
    load_dotenv()
    instruments = parse_instruments(
        os.getenv(
            "BOT_SYMBOLS",
            "TSLA:stock,BTC/USD:crypto",
        )
    )
    return BotConfig(
        broker_kind=parse_broker_kind(os.getenv("BROKER_KIND", "alpaca")),
        alpaca_api_key=os.getenv("ALPACA_API_KEY") or None,
        alpaca_api_secret=os.getenv("ALPACA_API_SECRET") or None,
        alpaca_paper=parse_bool(os.getenv("ALPACA_PAPER", "true")),
        alpaca_stock_feed=DataFeed(os.getenv("ALPACA_STOCK_FEED", "iex").strip().lower()),
        saxo_access_token=os.getenv("SAXO_ACCESS_TOKEN") or None,
        saxo_environment=os.getenv("SAXO_ENVIRONMENT", "sim").strip().lower(),
        saxo_account_key=os.getenv("SAXO_ACCOUNT_KEY") or None,
        saxo_default_exchange_id=os.getenv("SAXO_DEFAULT_EXCHANGE_ID", "XOSL").strip().upper(),
        saxo_client_key=os.getenv("SAXO_CLIENT_KEY") or None,
        saxo_instrument_map=parse_saxo_instrument_map(os.getenv("SAXO_INSTRUMENT_MAP")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        telegram_disable_notification=parse_bool(os.getenv("TELEGRAM_DISABLE_NOTIFICATION", "false")),
        telegram_message_thread_id=parse_optional_int(os.getenv("TELEGRAM_MESSAGE_THREAD_ID")),
        supabase_url=os.getenv("SUPABASE_URL") or None,
        supabase_anon_key=os.getenv("SUPABASE_ANON_KEY") or None,
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY") or None,
        dashboard_admin_password=os.getenv("DASHBOARD_ADMIN_PASSWORD") or None,
        instruments=tuple(instruments),
        strategy_kind=parse_strategy_kind(os.getenv("BOT_STRATEGY", "news_shock")),
        surprise_provider=os.getenv("SURPRISE_PROVIDER", "finnhub").strip().lower(),
        finnhub_api_key=os.getenv("FINNHUB_API_KEY") or None,
        global_news_enabled=parse_bool(os.getenv("GLOBAL_NEWS_ENABLED", "true")),
        vercel_webhook_logs_enabled=parse_bool(os.getenv("VERCEL_WEBHOOK_LOGS_ENABLED", "false")),
        vercel_webhook_scope=os.getenv("VERCEL_WEBHOOK_SCOPE") or None,
        vercel_webhook_environment=os.getenv("VERCEL_WEBHOOK_ENVIRONMENT", "production").strip().lower(),
        vercel_webhook_logs_since_minutes=parse_int("VERCEL_WEBHOOK_LOGS_SINCE_MINUTES", 15),
        vercel_webhook_cwd=parse_optional_path(os.getenv("VERCEL_WEBHOOK_CWD", ".")),
        official_rss_feeds_enabled=parse_bool(os.getenv("OFFICIAL_RSS_FEEDS_ENABLED", "false")),
        official_rss_feeds=parse_feed_map(
            os.getenv(
                "OFFICIAL_RSS_FEED_URLS",
                "sec_press=https://www.sec.gov/news/pressreleases.rss,"
                "fed_monetary=https://www.federalreserve.gov/feeds/press_monetary.xml,"
                "white_house=https://www.whitehouse.gov/briefing-room/feed/,"
                "eia=https://www.eia.gov/petroleum/feed/",
            )
        ),
        sec_tsla_submissions_enabled=parse_bool(os.getenv("SEC_TSLA_SUBMISSIONS_ENABLED", "false")),
        sec_api_user_agent=os.getenv("SEC_API_USER_AGENT", "trading-bot/0.1").strip(),
        x_webhook_enabled=parse_bool(os.getenv("X_WEBHOOK_ENABLED", "false")),
        x_consumer_secret=os.getenv("X_CONSUMER_SECRET") or None,
        x_webhook_url=os.getenv("X_WEBHOOK_URL") or None,
        x_filtered_stream_rule=os.getenv(
            "X_FILTERED_STREAM_RULE",
            '((from:elonmusk OR from:realDonaldTrump OR from:WhiteHouse OR from:SECGov '
            'OR from:federalreserve OR from:USTreasury OR from:saylor OR from:BitcoinMagazine '
            'OR from:tier10k OR from:KobeissiLetter OR from:WatcherGuru OR from:DeItaone '
            'OR from:unusual_whales OR from:zerohedge OR from:financialjuice OR from:dbnewsdesk) '
            '(bitcoin OR btc OR tesla OR tsla OR oil OR crude OR tariff OR sanctions OR reserve '
            'OR sec OR fed OR cpi OR ppi OR etf OR robotaxi OR fsd OR opec OR earnings OR regulation)) '
            '-is:retweet -is:reply',
        ).strip(),
        x_filtered_stream_rule_tag=os.getenv("X_FILTERED_STREAM_RULE_TAG", "trading-bot-realtime").strip(),
        x_stream_enabled=parse_bool(os.getenv("X_STREAM_ENABLED", "false")),
        x_stream_connect_timeout_seconds=parse_int("X_STREAM_CONNECT_TIMEOUT_SECONDS", 10),
        x_stream_read_timeout_seconds=parse_int("X_STREAM_READ_TIMEOUT_SECONDS", 90),
        x_stream_max_backoff_seconds=parse_int("X_STREAM_MAX_BACKOFF_SECONDS", 60),
        x_recent_search_enabled=parse_bool(os.getenv("X_RECENT_SEARCH_ENABLED", "false")),
        x_bearer_token=os.getenv("X_BEARER_TOKEN") or None,
        x_recent_search_query=os.getenv(
            "X_RECENT_SEARCH_QUERY",
            '((from:elonmusk OR from:realDonaldTrump OR from:WhiteHouse OR from:SECGov '
            'OR from:federalreserve OR from:USTreasury OR from:saylor OR from:BitcoinMagazine '
            'OR from:tier10k OR from:KobeissiLetter OR from:WatcherGuru OR from:DeItaone '
            'OR from:unusual_whales OR from:zerohedge OR from:financialjuice) '
            '(bitcoin OR btc OR tesla OR tsla OR oil OR crude OR tariff OR sanctions OR reserve '
            'OR sec OR fed OR cpi OR ppi OR etf OR robotaxi OR fsd OR opec OR earnings OR regulation)) '
            'lang:en -is:retweet -is:reply',
        ).strip(),
        x_recent_search_max_results=parse_int("X_RECENT_SEARCH_MAX_RESULTS", 25),
        short_window=parse_int("BOT_SHORT_WINDOW", 5),
        long_window=parse_int("BOT_LONG_WINDOW", 20),
        lookback_bars=parse_int("BOT_LOOKBACK_BARS", 60),
        loop_interval_seconds=parse_int("BOT_LOOP_INTERVAL_SECONDS", 60),
        risk_per_trade=parse_float("BOT_RISK_PER_TRADE", 0.005),
        cash_buffer=parse_float("BOT_CASH_BUFFER", 0.20),
        max_open_positions=parse_int("BOT_MAX_OPEN_POSITIONS", 2),
        min_notional_usd=parse_float("BOT_MIN_NOTIONAL_USD", 50),
        max_daily_loss_pct=parse_float("RISK_MAX_DAILY_LOSS_PCT", 0.015),
        news_shock_min_surprise=parse_float("NEWS_SHOCK_MIN_SURPRISE", 0.75),
        news_shock_min_confidence=parse_float("NEWS_SHOCK_MIN_CONFIDENCE", 0.80),
        news_shock_min_sentiment=parse_float("NEWS_SHOCK_MIN_SENTIMENT", 0.20),
        news_shock_min_source_count=parse_int("NEWS_SHOCK_MIN_SOURCE_COUNT", 2),
        news_shock_confirmation_bars=parse_int("NEWS_SHOCK_CONFIRMATION_BARS", 2),
        news_shock_volume_multiplier=parse_float("NEWS_SHOCK_VOLUME_MULTIPLIER", 1.5),
        news_shock_max_event_age_seconds=parse_int("NEWS_SHOCK_MAX_EVENT_AGE_SECONDS", 300),
        news_shock_realtime_window_seconds=parse_int("NEWS_SHOCK_REALTIME_WINDOW_SECONDS", 20),
        news_shock_btc_max_hold_minutes=parse_int("NEWS_SHOCK_BTC_MAX_HOLD_MINUTES", 360),
        news_shock_stock_flatten_minutes_before_close=parse_int(
            "NEWS_SHOCK_STOCK_FLATTEN_MINUTES_BEFORE_CLOSE", 10
        ),
        news_shock_target_leverage=parse_float("NEWS_SHOCK_TARGET_LEVERAGE", 10.0),
        news_shock_btc_min_surprise=parse_float("NEWS_SHOCK_BTC_MIN_SURPRISE", 0.15),
        news_shock_btc_min_confidence=parse_float("NEWS_SHOCK_BTC_MIN_CONFIDENCE", 0.55),
        news_shock_btc_min_sentiment=parse_float("NEWS_SHOCK_BTC_MIN_SENTIMENT", 0.08),
        news_shock_btc_min_source_count=parse_int("NEWS_SHOCK_BTC_MIN_SOURCE_COUNT", 1),
        news_shock_btc_confirmation_bars=parse_int("NEWS_SHOCK_BTC_CONFIRMATION_BARS", 1),
        news_shock_btc_volume_multiplier=parse_float("NEWS_SHOCK_BTC_VOLUME_MULTIPLIER", 1.05),
        news_shock_btc_momentum_fade_bars=parse_int("NEWS_SHOCK_BTC_MOMENTUM_FADE_BARS", 3),
        news_shock_btc_momentum_fade_min_profit_pct=parse_float(
            "NEWS_SHOCK_BTC_MOMENTUM_FADE_MIN_PROFIT_PCT", 0.003
        ),
        news_shock_btc_momentum_fade_from_high_pct=parse_float(
            "NEWS_SHOCK_BTC_MOMENTUM_FADE_FROM_HIGH_PCT", 0.0015
        ),
        oil_proxy_symbols=parse_csv(os.getenv("OIL_PROXY_SYMBOLS", "USO,XLE,OXY,XOM,CVX,SLB")),
        news_shock_oil_min_trade_score=parse_float("NEWS_SHOCK_OIL_MIN_TRADE_SCORE", 0.65),
        news_shock_oil_min_confidence=parse_float("NEWS_SHOCK_OIL_MIN_CONFIDENCE", 0.70),
        news_shock_oil_confirmation_bars=parse_int("NEWS_SHOCK_OIL_CONFIRMATION_BARS", 1),
        news_shock_oil_volume_multiplier=parse_float("NEWS_SHOCK_OIL_VOLUME_MULTIPLIER", 1.1),
        news_shock_oil_risk_per_trade=parse_float("NEWS_SHOCK_OIL_RISK_PER_TRADE", 0.004),
        earnings_provider=os.getenv("EARNINGS_PROVIDER", "finnhub").strip().lower(),
        earnings_lookahead_days=parse_int("EARNINGS_LOOKAHEAD_DAYS", 7),
        earnings_universe_max_size=parse_int("EARNINGS_UNIVERSE_MAX_SIZE", 500),
        earnings_market_cap_min_usd=parse_float("EARNINGS_MARKET_CAP_MIN_USD", 300_000_000),
        earnings_market_cap_max_usd=parse_float("EARNINGS_MARKET_CAP_MAX_USD", 10_000_000_000),
        earnings_min_price_usd=parse_float("EARNINGS_MIN_PRICE_USD", 3),
        earnings_min_avg_dollar_volume_usd=parse_float("EARNINGS_MIN_AVG_DOLLAR_VOLUME_USD", 2_000_000),
        earnings_min_eps_surprise_pct=parse_float("EARNINGS_MIN_EPS_SURPRISE_PCT", 0.12),
        earnings_min_revenue_surprise_pct=parse_float("EARNINGS_MIN_REVENUE_SURPRISE_PCT", 0.03),
        earnings_max_event_age_seconds=parse_int("EARNINGS_MAX_EVENT_AGE_SECONDS", 300),
        earnings_confirmation_bars=parse_int("EARNINGS_CONFIRMATION_BARS", 2),
        earnings_volume_multiplier=parse_float("EARNINGS_VOLUME_MULTIPLIER", 1.5),
        earnings_risk_per_trade=parse_float("EARNINGS_RISK_PER_TRADE", 0.0035),
        earnings_risk_multiplier_min=parse_float("EARNINGS_RISK_MULTIPLIER_MIN", 1.0),
        earnings_risk_multiplier_max=parse_float("EARNINGS_RISK_MULTIPLIER_MAX", 2.5),
        earnings_max_open_positions=parse_int("EARNINGS_MAX_OPEN_POSITIONS", 5),
        earnings_max_daily_loss_pct=parse_float("EARNINGS_MAX_DAILY_LOSS_PCT", 0.02),
        earnings_watchlist_limit=parse_int("EARNINGS_WATCHLIST_LIMIT", 20),
        earnings_telegram_watchlist_enabled=parse_bool(
            os.getenv("EARNINGS_TELEGRAM_WATCHLIST_ENABLED", "true")
        ),
        earnings_db_path=parse_optional_path(os.getenv("EARNINGS_DB_PATH", "data/earnings.sqlite")),
        social_watch_authors=parse_csv(os.getenv("SOCIAL_WATCH_AUTHORS", "elonmusk")),
        social_feed_path=parse_optional_path(os.getenv("SOCIAL_FEED_PATH")),
        social_lookback_minutes=parse_int("SOCIAL_LOOKBACK_MINUTES", 90),
        social_min_sentiment_score=parse_float("SOCIAL_MIN_SENTIMENT_SCORE", 0.6),
        social_min_engagement_score=parse_float("SOCIAL_MIN_ENGAGEMENT_SCORE", 50),
        news_feed_path=parse_optional_path(os.getenv("NEWS_FEED_PATH")),
        news_lookback_minutes=parse_int("NEWS_LOOKBACK_MINUTES", 180),
        news_buy_surprise_threshold=parse_float("NEWS_BUY_SURPRISE_THRESHOLD", 0.5),
        news_sell_surprise_threshold=parse_float("NEWS_SELL_SURPRISE_THRESHOLD", -0.5),
        news_min_sentiment_score=parse_float("NEWS_MIN_SENTIMENT_SCORE", 0.1),
        structured_events_path=parse_optional_path(os.getenv("STRUCTURED_EVENTS_PATH")),
        trade_log_path=parse_optional_path(os.getenv("TRADE_LOG_PATH", "logs/news_shock.jsonl")),
        dry_run=parse_bool(os.getenv("BOT_DRY_RUN", "true")),
    )


def parse_strategy_kind(raw_value: str) -> StrategyKind:
    normalized = raw_value.strip().lower()
    if normalized == "news":
        return StrategyKind.NEWS
    if normalized == "news_shock":
        return StrategyKind.NEWS_SHOCK
    if normalized == "earnings_surprise":
        return StrategyKind.EARNINGS_SURPRISE
    return StrategyKind(normalized)


def parse_broker_kind(raw_value: str) -> BrokerKind:
    return BrokerKind(raw_value.strip().lower())


def parse_instruments(raw_value: str) -> list[Instrument]:
    instruments: list[Instrument] = []
    for item in raw_value.split(","):
        chunk = item.strip()
        if not chunk:
            continue
        symbol, separator, asset_class = chunk.partition(":")
        if not separator:
            raise ValueError(
                f"Ugyldig BOT_SYMBOLS-verdi '{chunk}'. Bruk formatet SYMBOL:stock."
            )
        instruments.append(
            Instrument(
                symbol=symbol.strip(),
                asset_class=AssetClass(asset_class.strip().lower()),
            )
        )
    if not instruments:
        raise ValueError("BOT_SYMBOLS må inneholde minst ett instrument.")
    return instruments


def parse_bool(raw_value: str) -> bool:
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def parse_optional_int(raw_value: str | None) -> int | None:
    if raw_value is None:
        return None
    value = raw_value.strip()
    if not value:
        return None
    return int(value)


def parse_csv(raw_value: str | None) -> tuple[str, ...]:
    if raw_value is None:
        return ()
    return tuple(chunk.strip() for chunk in raw_value.split(",") if chunk.strip())


def parse_feed_map(raw_value: str | None) -> tuple[tuple[str, str], ...]:
    if raw_value is None:
        return ()
    mappings: list[tuple[str, str]] = []
    for chunk in raw_value.split(","):
        item = chunk.strip()
        if not item:
            continue
        name, separator, url = item.partition("=")
        if not separator:
            raise ValueError(
                f"Ugyldig OFFICIAL_RSS_FEED_URLS-verdi '{item}'. Bruk formatet navn=https://example.com/feed.xml."
            )
        mappings.append((name.strip(), url.strip()))
    return tuple(mappings)


def parse_saxo_instrument_map(raw_value: str | None) -> tuple[tuple[str, int], ...]:
    if raw_value is None:
        return ()
    mappings: list[tuple[str, int]] = []
    for chunk in raw_value.split(","):
        item = chunk.strip()
        if not item:
            continue
        symbol, separator, uic = item.partition("=")
        if not separator:
            raise ValueError(
                f"Ugyldig SAXO_INSTRUMENT_MAP-verdi '{item}'. Bruk formatet EQNR=12345."
            )
        mappings.append((symbol.strip(), int(uic.strip())))
    return tuple(mappings)


def parse_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    return int(raw_value) if raw_value is not None else default


def parse_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    return float(raw_value) if raw_value is not None else default


def parse_optional_path(raw_value: str | None) -> Path | None:
    if raw_value is None or not raw_value.strip():
        return None
    return Path(raw_value).expanduser()
