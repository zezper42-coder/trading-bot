from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class AssetClass(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"


class BrokerKind(str, Enum):
    ALPACA = "alpaca"
    SAXO = "saxo"


class StrategyKind(str, Enum):
    TECHNICAL = "technical"
    SOCIAL = "social"
    NEWS = "news"
    NEWS_SHOCK = "news_shock"
    EARNINGS_SURPRISE = "earnings_surprise"


class StructuredEventCategory(str, Enum):
    EARNINGS = "earnings"
    MACRO = "macro"
    GEOPOLITICAL = "geopolitical"
    ENERGY_POLICY = "energy_policy"
    OTHER = "other"


class SignalAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    SHORT = "short"
    COVER = "cover"
    HOLD = "hold"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Instrument:
    symbol: str
    asset_class: AssetClass


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class HeadlineContext:
    headline: str
    source: str
    created_at: datetime


@dataclass(frozen=True)
class StructuredEvent:
    event_id: str
    source: str
    instrument_scope: tuple[str, ...]
    category: StructuredEventCategory
    published_at: datetime
    headline: str
    actual_value: float | None
    expected_value: float | None
    surprise_score: float
    sentiment_score: float
    confidence_score: float
    is_scheduled: bool
    headline_context: tuple[HeadlineContext, ...] = ()
    supporting_sources: tuple[str, ...] = ()
    source_count: int = 1
    corroboration_score: float = 1.0
    theme: str = "general_news"
    topic_tags: tuple[str, ...] = ()
    entity_tags: tuple[str, ...] = ()
    direction_score: float = 0.0
    magnitude_score: float = 0.0
    unexpectedness_score: float = 0.0
    trade_score: float = 0.0


@dataclass(frozen=True)
class Signal:
    instrument: Instrument
    action: SignalAction
    price: float
    reason: str
    event_id: str | None = None
    source: str | None = None
    anchor_price: float | None = None
    stop_price: float | None = None
    actual_value: float | None = None
    expected_value: float | None = None
    surprise_score: float | None = None
    sentiment_score: float | None = None
    confidence_score: float | None = None
    source_count: int | None = None
    corroboration_score: float | None = None
    supporting_sources: tuple[str, ...] = ()
    exit_reason: str | None = None
    target_leverage: float = 1.0
    highest_price: float | None = None
    lowest_price: float | None = None
    trailing_active: bool | None = None
    trailing_stop_price: float | None = None
    risk_multiplier: float = 1.0
    risk_per_trade_override: float | None = None
    theme: str | None = None
    topic_tags: tuple[str, ...] = ()
    entity_tags: tuple[str, ...] = ()
    direction_score: float | None = None
    magnitude_score: float | None = None
    unexpectedness_score: float | None = None
    trade_score: float | None = None


@dataclass(frozen=True)
class SocialPost:
    id: str
    source: str
    author: str
    created_at: datetime
    text: str
    symbols: tuple[str, ...]
    sentiment_score: float
    engagement_score: float


@dataclass(frozen=True)
class NewsEvent:
    id: str
    source: str
    headline: str
    created_at: datetime
    symbols: tuple[str, ...]
    sentiment_score: float
    surprise_score: float
    expected_value: float | None = None
    actual_value: float | None = None


@dataclass
class ManagedPosition:
    instrument: Instrument
    qty: float
    entry_price: float
    entry_time: datetime
    highest_price: float
    lowest_price: float
    stop_price: float
    initial_stop_price: float
    trailing_active: bool
    trailing_stop_price: float | None
    event_id: str | None
    source: str | None
    anchor_price: float | None
    actual_value: float | None
    expected_value: float | None
    surprise_score: float | None
    sentiment_score: float | None
    confidence_score: float | None
    source_count: int | None = None
    corroboration_score: float | None = None
    supporting_sources: tuple[str, ...] = ()
    target_leverage: float = 1.0
    theme: str | None = None


@dataclass(frozen=True)
class StrategyContext:
    instrument: Instrument
    bars: list[Bar]
    position_qty: float
    now: datetime
    live_price: float | None = None
    live_timestamp: datetime | None = None
    managed_position: ManagedPosition | None = None
    social_posts: tuple[SocialPost, ...] = ()
    news_events: tuple[NewsEvent, ...] = ()
    structured_events: tuple[StructuredEvent, ...] = ()
    earnings_releases: tuple["EarningsRelease", ...] = ()


@dataclass(frozen=True)
class AccountSnapshot:
    equity: float
    cash: float
    buying_power: float


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    market_value: float
    avg_entry_price: float


@dataclass(frozen=True)
class BrokerCapabilities:
    name: str
    max_leverage: float
    supports_crypto_margin: bool
    supports_fractional_shares: bool = True


@dataclass(frozen=True)
class OrderPlan:
    instrument: Instrument
    side: OrderSide
    qty: float | None = None
    notional: float | None = None
    limit_price: float | None = None
    extended_hours: bool = False
    capped_by_buying_power: bool = False
    target_leverage: float = 1.0
    risk_multiplier: float = 1.0
    risk_per_trade_used: float | None = None
    event_id: str | None = None
    signal_reason: str | None = None


@dataclass(frozen=True)
class BotControlState:
    bot_enabled: bool = True
    dry_run_override: bool | None = None
    emergency_stop_active: bool = False
    updated_at: datetime | None = None


@dataclass(frozen=True)
class StrategySetting:
    theme: str
    enabled: bool = True
    min_surprise: float | None = None
    min_confidence: float | None = None
    min_sentiment: float | None = None
    min_source_count: int | None = None
    confirmation_bars: int | None = None
    volume_multiplier: float | None = None
    max_event_age_seconds: int | None = None
    risk_per_trade: float | None = None
    risk_multiplier_min: float | None = None
    risk_multiplier_max: float | None = None
    min_trade_score: float | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class BacktestTrade:
    timestamp: datetime
    side: OrderSide
    price: float
    qty: float
    notional: float
    event_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class BacktestResult:
    instrument: Instrument
    trades: list[BacktestTrade]
    initial_cash: float
    ending_cash: float
    ending_position_qty: float
    ending_equity: float
    return_pct: float


@dataclass(frozen=True)
class EarningsCandidate:
    symbol: str
    earnings_date: date
    earnings_hour: str | None
    instrument: Instrument
    last_price: float
    market_cap_usd: float
    avg_dollar_volume_usd: float
    exchange: str
    mic: str
    company_name: str
    industry: str | None
    eps_estimate: float
    revenue_estimate: float
    extended_hours_eligible: bool


@dataclass(frozen=True)
class ConsensusSnapshot:
    symbol: str
    period: str
    captured_at: datetime
    eps_estimate: float
    revenue_estimate: float
    quarter: int | None = None
    year: int | None = None
    eps_actual: float | None = None
    revenue_actual: float | None = None
    number_analysts_eps: int | None = None
    number_analysts_revenue: int | None = None
    source: str = "finnhub"


@dataclass(frozen=True)
class PreEarningsAnalysis:
    candidate: EarningsCandidate
    analysis_at: datetime
    score: float
    eps_revision_score: float
    revenue_revision_score: float
    surprise_quality_score: float
    filing_freshness_score: float
    liquidity_volatility_score: float
    reasons: tuple[str, ...] = ()
    consensus: ConsensusSnapshot | None = None


@dataclass(frozen=True)
class EarningsRelease:
    event_id: str
    symbol: str
    earnings_date: date
    observed_at: datetime
    published_at: datetime
    hour: str | None
    quarter: int | None
    year: int | None
    eps_actual: float
    eps_estimate: float
    revenue_actual: float
    revenue_estimate: float
    eps_surprise_pct: float
    revenue_surprise_pct: float
    anchor_price: float | None
    source: str = "finnhub"
    in_universe: bool = True
    extended_hours_eligible: bool = True


def canonical_symbol(symbol: str) -> str:
    return (
        symbol.strip()
        .upper()
        .replace("/", "")
        .replace("-", "")
        .replace(" ", "")
    )


def symbol_in_scope(symbol: str, scope: tuple[str, ...]) -> bool:
    symbol_key = canonical_symbol(symbol)
    return any(canonical_symbol(candidate) == symbol_key for candidate in scope)


def unique_headlines(headlines: tuple[HeadlineContext, ...]) -> tuple[HeadlineContext, ...]:
    seen: set[tuple[str, str]] = set()
    unique: list[HeadlineContext] = []
    for item in headlines:
        key = (item.source, item.headline)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(unique)
