from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from alpaca.data.enums import DataFeed
from dotenv import load_dotenv

from trading_bot.domain import AssetClass, Instrument, StrategyKind


@dataclass(frozen=True)
class BotConfig:
    alpaca_api_key: str | None
    alpaca_api_secret: str | None
    alpaca_paper: bool
    alpaca_stock_feed: DataFeed
    instruments: tuple[Instrument, ...]
    strategy_kind: StrategyKind
    surprise_provider: str
    finnhub_api_key: str | None
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
    news_shock_confirmation_bars: int
    news_shock_volume_multiplier: float
    news_shock_max_event_age_seconds: int
    news_shock_btc_max_hold_minutes: int
    news_shock_stock_flatten_minutes_before_close: int
    news_shock_target_leverage: float
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
        if self.alpaca_api_key and self.alpaca_api_secret:
            return
        raise RuntimeError(
            "ALPACA_API_KEY og ALPACA_API_SECRET må være satt for paper trading."
        )

    def require_market_data_credentials(self, asset_class: AssetClass) -> None:
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
        if self.surprise_provider == "finnhub" and self.finnhub_api_key:
            return
        raise RuntimeError(
            "news_shock krever enten STRUCTURED_EVENTS_PATH eller FINNHUB_API_KEY."
        )


def load_config() -> BotConfig:
    load_dotenv()
    instruments = parse_instruments(
        os.getenv(
            "BOT_SYMBOLS",
            "TSLA:stock,BTC/USD:crypto",
        )
    )
    return BotConfig(
        alpaca_api_key=os.getenv("ALPACA_API_KEY") or None,
        alpaca_api_secret=os.getenv("ALPACA_API_SECRET") or None,
        alpaca_paper=parse_bool(os.getenv("ALPACA_PAPER", "true")),
        alpaca_stock_feed=DataFeed(os.getenv("ALPACA_STOCK_FEED", "iex").strip().lower()),
        instruments=tuple(instruments),
        strategy_kind=parse_strategy_kind(os.getenv("BOT_STRATEGY", "news_shock")),
        surprise_provider=os.getenv("SURPRISE_PROVIDER", "finnhub").strip().lower(),
        finnhub_api_key=os.getenv("FINNHUB_API_KEY") or None,
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
        news_shock_confirmation_bars=parse_int("NEWS_SHOCK_CONFIRMATION_BARS", 2),
        news_shock_volume_multiplier=parse_float("NEWS_SHOCK_VOLUME_MULTIPLIER", 1.5),
        news_shock_max_event_age_seconds=parse_int("NEWS_SHOCK_MAX_EVENT_AGE_SECONDS", 300),
        news_shock_btc_max_hold_minutes=parse_int("NEWS_SHOCK_BTC_MAX_HOLD_MINUTES", 360),
        news_shock_stock_flatten_minutes_before_close=parse_int(
            "NEWS_SHOCK_STOCK_FLATTEN_MINUTES_BEFORE_CLOSE", 10
        ),
        news_shock_target_leverage=parse_float("NEWS_SHOCK_TARGET_LEVERAGE", 10.0),
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
    return StrategyKind(normalized)


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


def parse_csv(raw_value: str | None) -> tuple[str, ...]:
    if raw_value is None:
        return ()
    return tuple(chunk.strip() for chunk in raw_value.split(",") if chunk.strip())


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
