from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class AssetClass(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"


class StrategyKind(str, Enum):
    TECHNICAL = "technical"
    SOCIAL = "social"
    NEWS = "news"
    NEWS_SHOCK = "news_shock"


class StructuredEventCategory(str, Enum):
    EARNINGS = "earnings"
    MACRO = "macro"
    OTHER = "other"


class SignalAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
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
    exit_reason: str | None = None
    target_leverage: float = 1.0
    highest_price: float | None = None
    trailing_active: bool | None = None
    trailing_stop_price: float | None = None


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
    target_leverage: float = 1.0


@dataclass(frozen=True)
class StrategyContext:
    instrument: Instrument
    bars: list[Bar]
    position_qty: float
    now: datetime
    managed_position: ManagedPosition | None = None
    social_posts: tuple[SocialPost, ...] = ()
    news_events: tuple[NewsEvent, ...] = ()
    structured_events: tuple[StructuredEvent, ...] = ()


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


@dataclass(frozen=True)
class OrderPlan:
    instrument: Instrument
    side: OrderSide
    qty: float | None = None
    notional: float | None = None
    capped_by_buying_power: bool = False
    target_leverage: float = 1.0
    event_id: str | None = None
    signal_reason: str | None = None


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
