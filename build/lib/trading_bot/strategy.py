from __future__ import annotations

from datetime import datetime, timedelta
from statistics import fmean
from typing import Protocol
from zoneinfo import ZoneInfo

from trading_bot.domain import (
    AssetClass,
    Bar,
    Signal,
    SignalAction,
    StrategyContext,
    StructuredEvent,
)


def atr(bars: list[Bar], period: int = 14) -> float | None:
    if len(bars) < period + 1:
        return None
    true_ranges: list[float] = []
    for current, previous in zip(bars[-period:], bars[-period - 1 : -1]):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return fmean(true_ranges)


class TradingStrategy(Protocol):
    minimum_bars_required: int

    def evaluate(self, context: StrategyContext) -> Signal:
        ...


class MovingAverageCrossStrategy:
    def __init__(self, short_window: int, long_window: int) -> None:
        if short_window <= 0 or long_window <= 0:
            raise ValueError("Moving-average vinduer må være positive.")
        if short_window >= long_window:
            raise ValueError("Kort vindu må være mindre enn langt vindu.")
        self.short_window = short_window
        self.long_window = long_window
        self.minimum_bars_required = long_window + 1

    def evaluate(self, context: StrategyContext) -> Signal:
        bars = context.bars
        if len(bars) < self.minimum_bars_required:
            return Signal(
                instrument=context.instrument,
                action=SignalAction.HOLD,
                price=bars[-1].close,
                reason="Ikke nok historikk for strategi.",
            )

        closes = [bar.close for bar in bars]
        previous_short = fmean(closes[-self.short_window - 1 : -1])
        previous_long = fmean(closes[-self.long_window - 1 : -1])
        current_short = fmean(closes[-self.short_window :])
        current_long = fmean(closes[-self.long_window :])
        last_price = closes[-1]

        if previous_short <= previous_long and current_short > current_long and context.position_qty <= 0:
            return Signal(
                instrument=context.instrument,
                action=SignalAction.BUY,
                price=last_price,
                reason="Bullish crossover.",
            )

        if previous_short >= previous_long and current_short < current_long and context.position_qty > 0:
            return Signal(
                instrument=context.instrument,
                action=SignalAction.SELL,
                price=last_price,
                reason="Bearish crossover.",
            )

        return Signal(
            instrument=context.instrument,
            action=SignalAction.HOLD,
            price=last_price,
            reason="Ingen ny crossover.",
        )


class SocialReactionStrategy:
    def __init__(
        self,
        watched_authors: tuple[str, ...],
        min_sentiment_score: float,
        min_engagement_score: float,
    ) -> None:
        self.watched_authors = {author.lower() for author in watched_authors if author.strip()}
        self.min_sentiment_score = min_sentiment_score
        self.min_engagement_score = min_engagement_score
        self.minimum_bars_required = 1

    def evaluate(self, context: StrategyContext) -> Signal:
        latest_price = context.bars[-1].close
        if not context.social_posts:
            return Signal(
                instrument=context.instrument,
                action=SignalAction.HOLD,
                price=latest_price,
                reason="Ingen relevante sosiale poster i feeden.",
            )

        for post in context.social_posts:
            if self.watched_authors and post.author not in self.watched_authors:
                continue
            if post.engagement_score < self.min_engagement_score:
                continue

            if post.sentiment_score >= self.min_sentiment_score and context.position_qty <= 0:
                return Signal(
                    instrument=context.instrument,
                    action=SignalAction.BUY,
                    price=latest_price,
                    reason=f"Positiv post fra {post.author} med sentiment {post.sentiment_score:.2f}.",
                )

            if post.sentiment_score <= -self.min_sentiment_score and context.position_qty > 0:
                return Signal(
                    instrument=context.instrument,
                    action=SignalAction.SELL,
                    price=latest_price,
                    reason=f"Negativ post fra {post.author} med sentiment {post.sentiment_score:.2f}.",
                )

        return Signal(
            instrument=context.instrument,
            action=SignalAction.HOLD,
            price=latest_price,
            reason="Poster funnet, men uten tilstrekkelig styrke eller feil posisjonstilstand.",
        )


class NewsSurpriseStrategy:
    def __init__(
        self,
        buy_surprise_threshold: float,
        sell_surprise_threshold: float,
        min_sentiment_score: float,
    ) -> None:
        if sell_surprise_threshold > 0:
            raise ValueError("sell_surprise_threshold må være negativ eller null.")
        self.buy_surprise_threshold = buy_surprise_threshold
        self.sell_surprise_threshold = sell_surprise_threshold
        self.min_sentiment_score = min_sentiment_score
        self.minimum_bars_required = 1

    def evaluate(self, context: StrategyContext) -> Signal:
        latest_price = context.bars[-1].close
        if not context.news_events:
            return Signal(
                instrument=context.instrument,
                action=SignalAction.HOLD,
                price=latest_price,
                reason="Ingen relevante nyhetshendelser i feeden.",
            )

        for event in context.news_events:
            if (
                event.surprise_score >= self.buy_surprise_threshold
                and event.sentiment_score >= self.min_sentiment_score
                and context.position_qty <= 0
            ):
                return Signal(
                    instrument=context.instrument,
                    action=SignalAction.BUY,
                    price=latest_price,
                    reason=_format_news_reason("Positiv nyhetsoverraskelse", event),
                )

            if event.surprise_score <= self.sell_surprise_threshold and context.position_qty > 0:
                return Signal(
                    instrument=context.instrument,
                    action=SignalAction.SELL,
                    price=latest_price,
                    reason=_format_news_reason("Negativ nyhetsoverraskelse", event),
                )

        return Signal(
            instrument=context.instrument,
            action=SignalAction.HOLD,
            price=latest_price,
            reason="Nyheter funnet, men ingen hendelse passerte tersklene.",
        )


class NewsShockStrategy:
    def __init__(
        self,
        min_surprise: float,
        min_confidence: float,
        min_sentiment: float,
        confirmation_bars: int,
        volume_multiplier: float,
        max_event_age_seconds: int,
        btc_max_hold_minutes: int,
        stock_flatten_minutes_before_close: int,
        target_leverage: float,
    ) -> None:
        self.min_surprise = min_surprise
        self.min_confidence = min_confidence
        self.min_sentiment = min_sentiment
        self.confirmation_bars = confirmation_bars
        self.volume_multiplier = volume_multiplier
        self.max_event_age_seconds = max_event_age_seconds
        self.btc_max_hold_minutes = btc_max_hold_minutes
        self.stock_flatten_minutes_before_close = stock_flatten_minutes_before_close
        self.target_leverage = target_leverage
        self.minimum_bars_required = max(21, 15, confirmation_bars + 2)

    def evaluate(self, context: StrategyContext) -> Signal:
        latest_bar = context.bars[-1]

        if context.managed_position is not None and context.position_qty > 0:
            exit_signal = self._evaluate_open_position(context)
            if exit_signal is not None:
                return exit_signal

        for event in context.structured_events:
            if context.position_qty > 0:
                break
            signal = self._evaluate_entry_event(context, event)
            if signal is not None:
                return signal

        return Signal(
            instrument=context.instrument,
            action=SignalAction.HOLD,
            price=latest_bar.close,
            reason="Ingen structured surprise-event passerte entry-filtrene.",
        )

    def _evaluate_open_position(self, context: StrategyContext) -> Signal | None:
        managed_position = context.managed_position
        latest_bar = context.bars[-1]
        current_atr = atr(context.bars, 14)
        if current_atr is None:
            return None

        highest_price = max(managed_position.highest_price, latest_bar.high)
        profit_pct = (latest_bar.close / managed_position.entry_price) - 1
        trailing_active = managed_position.trailing_active or profit_pct >= 0.008
        trailing_stop_price = managed_position.trailing_stop_price
        if trailing_active:
            trailing_stop_price = highest_price - (1.5 * current_atr)
        effective_stop = max(
            managed_position.initial_stop_price,
            trailing_stop_price if trailing_stop_price is not None else managed_position.initial_stop_price,
        )

        if latest_bar.close <= effective_stop:
            reason = "trailing_stop" if trailing_active else "hard_stop"
            return Signal(
                instrument=context.instrument,
                action=SignalAction.SELL,
                price=latest_bar.close,
                reason=f"Exit via {reason}.",
                event_id=managed_position.event_id,
                source=managed_position.source,
                exit_reason=reason,
                stop_price=effective_stop,
                highest_price=highest_price,
                trailing_active=trailing_active,
                trailing_stop_price=trailing_stop_price,
            )

        if (
            context.instrument.asset_class is AssetClass.STOCK
            and _is_near_us_market_close(context.now, self.stock_flatten_minutes_before_close)
        ):
            return Signal(
                instrument=context.instrument,
                action=SignalAction.SELL,
                price=latest_bar.close,
                reason="Stenger aksjeposisjon før market close.",
                event_id=managed_position.event_id,
                source=managed_position.source,
                exit_reason="pre_close_flatten",
                stop_price=effective_stop,
                highest_price=highest_price,
                trailing_active=trailing_active,
                trailing_stop_price=trailing_stop_price,
            )

        if (
            context.instrument.asset_class is AssetClass.CRYPTO
            and context.now >= managed_position.entry_time + timedelta(minutes=self.btc_max_hold_minutes)
        ):
            return Signal(
                instrument=context.instrument,
                action=SignalAction.SELL,
                price=latest_bar.close,
                reason="Stenger BTC-posisjon etter maks holdetid.",
                event_id=managed_position.event_id,
                source=managed_position.source,
                exit_reason="max_hold_time",
                stop_price=effective_stop,
                highest_price=highest_price,
                trailing_active=trailing_active,
                trailing_stop_price=trailing_stop_price,
            )

        return Signal(
            instrument=context.instrument,
            action=SignalAction.HOLD,
            price=latest_bar.close,
            reason="Posisjon holdes videre.",
            event_id=managed_position.event_id,
            source=managed_position.source,
            stop_price=effective_stop,
            highest_price=highest_price,
            trailing_active=trailing_active,
            trailing_stop_price=trailing_stop_price,
        )

    def _evaluate_entry_event(
        self,
        context: StrategyContext,
        event: StructuredEvent,
    ) -> Signal | None:
        latest_bar = context.bars[-1]
        event_age = (context.now - event.published_at).total_seconds()
        if event_age > self.max_event_age_seconds:
            return None
        if event.surprise_score < self.min_surprise:
            return None
        if event.confidence_score < self.min_confidence:
            return None
        if event.sentiment_score < self.min_sentiment:
            return None

        anchor_price = _event_anchor_price(context.bars, event)
        if anchor_price is None:
            return None
        if not _has_confirmation_bars(context.bars, event, anchor_price, self.confirmation_bars):
            return None
        if not _has_volume_confirmation(context.bars, self.volume_multiplier):
            return None

        current_atr = atr(context.bars, 14)
        if current_atr is None:
            return None
        stop_price = latest_bar.close - (1.5 * current_atr)
        if stop_price >= latest_bar.close:
            return None

        return Signal(
            instrument=context.instrument,
            action=SignalAction.BUY,
            price=latest_bar.close,
            reason=(
                f"Structured surprise-event {event.event_id} bekreftet av {self.confirmation_bars} "
                "bar(er) og volum."
            ),
            event_id=event.event_id,
            source=event.source,
            anchor_price=anchor_price,
            stop_price=stop_price,
            actual_value=event.actual_value,
            expected_value=event.expected_value,
            surprise_score=event.surprise_score,
            sentiment_score=event.sentiment_score,
            confidence_score=event.confidence_score,
            target_leverage=self.target_leverage,
            highest_price=latest_bar.high,
            trailing_active=False,
            trailing_stop_price=None,
        )


def _format_news_reason(prefix: str, event) -> str:
    if event.actual_value is not None and event.expected_value is not None:
        return (
            f"{prefix}: actual={event.actual_value:.2f}, "
            f"expected={event.expected_value:.2f}, surprise={event.surprise_score:.2f}."
        )
    return f"{prefix}: surprise={event.surprise_score:.2f}, sentiment={event.sentiment_score:.2f}."


def _event_anchor_price(bars: list[Bar], event: StructuredEvent) -> float | None:
    eligible = [bar.close for bar in bars if bar.timestamp <= event.published_at]
    if eligible:
        return eligible[-1]
    return None


def _has_confirmation_bars(
    bars: list[Bar],
    event: StructuredEvent,
    anchor_price: float,
    confirmation_bars: int,
) -> bool:
    post_event_bars = [bar for bar in bars if bar.timestamp > event.published_at]
    if len(post_event_bars) < confirmation_bars:
        return False
    confirmation_slice = post_event_bars[-confirmation_bars:]
    return all(bar.close > anchor_price for bar in confirmation_slice)


def _has_volume_confirmation(bars: list[Bar], volume_multiplier: float) -> bool:
    if len(bars) < 21:
        return False
    latest_bar = bars[-1]
    previous_bars = bars[-21:-1]
    average_volume = fmean(bar.volume for bar in previous_bars)
    return latest_bar.volume >= average_volume * volume_multiplier


def _is_near_us_market_close(now: datetime, flatten_minutes_before_close: int) -> bool:
    if not isinstance(now, datetime):
        return False
    eastern = ZoneInfo("America/New_York")
    now_eastern = now.astimezone(eastern)
    close_minutes = (16 * 60) - flatten_minutes_before_close
    current_minutes = now_eastern.hour * 60 + now_eastern.minute
    return now_eastern.weekday() < 5 and current_minutes >= close_minutes
