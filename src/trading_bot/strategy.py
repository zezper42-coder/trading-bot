from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from statistics import fmean
from typing import Protocol
from zoneinfo import ZoneInfo

from trading_bot.domain import (
    AssetClass,
    Bar,
    EarningsRelease,
    Signal,
    SignalAction,
    StrategySetting,
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


@dataclass(frozen=True)
class NewsShockProfile:
    min_surprise: float
    min_confidence: float
    min_sentiment: float
    min_source_count: int
    confirmation_bars: int
    volume_multiplier: float
    enabled: bool = True
    min_trade_score: float | None = None
    risk_per_trade_override: float | None = None
    allow_sparse_volume_confirmation: bool = False


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
        min_source_count: int,
        confirmation_bars: int,
        volume_multiplier: float,
        max_event_age_seconds: int,
        realtime_window_seconds: int,
        btc_max_hold_minutes: int,
        stock_flatten_minutes_before_close: int,
        target_leverage: float,
        btc_min_surprise: float,
        btc_min_confidence: float,
        btc_min_sentiment: float,
        btc_min_source_count: int,
        btc_confirmation_bars: int,
        btc_volume_multiplier: float,
        btc_momentum_fade_bars: int,
        btc_momentum_fade_min_profit_pct: float,
        btc_momentum_fade_from_high_pct: float,
        oil_proxy_symbols: tuple[str, ...],
        oil_min_trade_score: float,
        oil_min_confidence: float,
        oil_confirmation_bars: int,
        oil_volume_multiplier: float,
        oil_risk_per_trade: float,
        strategy_settings: dict[str, StrategySetting] | None = None,
    ) -> None:
        self.default_profile = NewsShockProfile(
            min_surprise=min_surprise,
            min_confidence=min_confidence,
            min_sentiment=min_sentiment,
            min_source_count=min_source_count,
            confirmation_bars=confirmation_bars,
            volume_multiplier=volume_multiplier,
            allow_sparse_volume_confirmation=False,
        )
        self.btc_profile = NewsShockProfile(
            min_surprise=btc_min_surprise,
            min_confidence=btc_min_confidence,
            min_sentiment=btc_min_sentiment,
            min_source_count=btc_min_source_count,
            confirmation_bars=btc_confirmation_bars,
            volume_multiplier=btc_volume_multiplier,
            allow_sparse_volume_confirmation=True,
        )
        self.oil_profile = NewsShockProfile(
            min_surprise=0.0,
            min_confidence=oil_min_confidence,
            min_sentiment=0.0,
            min_source_count=1,
            confirmation_bars=oil_confirmation_bars,
            volume_multiplier=oil_volume_multiplier,
            min_trade_score=oil_min_trade_score,
            risk_per_trade_override=oil_risk_per_trade,
            allow_sparse_volume_confirmation=False,
        )
        self.max_event_age_seconds = max_event_age_seconds
        self.realtime_window_seconds = realtime_window_seconds
        self.btc_max_hold_minutes = btc_max_hold_minutes
        self.stock_flatten_minutes_before_close = stock_flatten_minutes_before_close
        self.target_leverage = target_leverage
        self.btc_momentum_fade_bars = btc_momentum_fade_bars
        self.btc_momentum_fade_min_profit_pct = btc_momentum_fade_min_profit_pct
        self.btc_momentum_fade_from_high_pct = btc_momentum_fade_from_high_pct
        self.oil_proxy_symbols = {symbol.upper() for symbol in oil_proxy_symbols}
        self.strategy_settings = strategy_settings or {}
        self.minimum_bars_required = max(
            21,
            15,
            confirmation_bars + 2,
            btc_confirmation_bars + 2,
            oil_confirmation_bars + 2,
            btc_momentum_fade_bars + 2,
        )

    def evaluate(self, context: StrategyContext) -> Signal:
        latest_bar = context.bars[-1]

        if context.managed_position is not None and context.position_qty != 0:
            exit_signal = self._evaluate_open_position(context)
            if exit_signal is not None:
                return exit_signal

        for event in context.structured_events:
            if context.position_qty != 0:
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
        if managed_position.qty < 0:
            return self._evaluate_open_short_position(context, managed_position, latest_bar, current_atr)

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
                source_count=managed_position.source_count,
                corroboration_score=managed_position.corroboration_score,
                supporting_sources=managed_position.supporting_sources,
                theme=managed_position.theme,
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
                source_count=managed_position.source_count,
                corroboration_score=managed_position.corroboration_score,
                supporting_sources=managed_position.supporting_sources,
                theme=managed_position.theme,
            )

        if (
            context.instrument.asset_class is AssetClass.CRYPTO
            and profit_pct >= self.btc_momentum_fade_min_profit_pct
            and _has_momentum_faded(
                context.bars,
                highest_price=highest_price,
                fade_bars=self.btc_momentum_fade_bars,
                min_pullback_pct=self.btc_momentum_fade_from_high_pct,
            )
        ):
            return Signal(
                instrument=context.instrument,
                action=SignalAction.SELL,
                price=latest_bar.close,
                reason="Stenger BTC-posisjon når momentet avtar etter nyhetsdrevet stigning.",
                event_id=managed_position.event_id,
                source=managed_position.source,
                exit_reason="momentum_fade",
                stop_price=effective_stop,
                highest_price=highest_price,
                trailing_active=trailing_active,
                trailing_stop_price=trailing_stop_price,
                source_count=managed_position.source_count,
                corroboration_score=managed_position.corroboration_score,
                supporting_sources=managed_position.supporting_sources,
                theme=managed_position.theme,
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
                source_count=managed_position.source_count,
                corroboration_score=managed_position.corroboration_score,
                supporting_sources=managed_position.supporting_sources,
                theme=managed_position.theme,
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
            source_count=managed_position.source_count,
            corroboration_score=managed_position.corroboration_score,
            supporting_sources=managed_position.supporting_sources,
            theme=managed_position.theme,
        )

    def _evaluate_open_short_position(self, context, managed_position, latest_bar, current_atr) -> Signal | None:
        lowest_price = min(managed_position.lowest_price, latest_bar.low)
        profit_pct = 1 - (latest_bar.close / managed_position.entry_price)
        trailing_active = managed_position.trailing_active or profit_pct >= 0.008
        trailing_stop_price = managed_position.trailing_stop_price
        if trailing_active:
            trailing_stop_price = lowest_price + (1.5 * current_atr)
        effective_stop = min(
            managed_position.initial_stop_price,
            trailing_stop_price if trailing_stop_price is not None else managed_position.initial_stop_price,
        )

        if latest_bar.close >= effective_stop:
            reason = "trailing_stop" if trailing_active else "hard_stop"
            return Signal(
                instrument=context.instrument,
                action=SignalAction.COVER,
                price=latest_bar.close,
                reason=f"Exit via {reason}.",
                event_id=managed_position.event_id,
                source=managed_position.source,
                exit_reason=reason,
                stop_price=effective_stop,
                highest_price=managed_position.highest_price,
                lowest_price=lowest_price,
                trailing_active=trailing_active,
                trailing_stop_price=trailing_stop_price,
                source_count=managed_position.source_count,
                corroboration_score=managed_position.corroboration_score,
                supporting_sources=managed_position.supporting_sources,
                theme=managed_position.theme,
            )

        if (
            context.instrument.asset_class is AssetClass.STOCK
            and _is_near_us_market_close(context.now, self.stock_flatten_minutes_before_close)
        ):
            return Signal(
                instrument=context.instrument,
                action=SignalAction.COVER,
                price=latest_bar.close,
                reason="Dekker aksjeshort før market close.",
                event_id=managed_position.event_id,
                source=managed_position.source,
                exit_reason="pre_close_flatten",
                stop_price=effective_stop,
                highest_price=managed_position.highest_price,
                lowest_price=lowest_price,
                trailing_active=trailing_active,
                trailing_stop_price=trailing_stop_price,
                source_count=managed_position.source_count,
                corroboration_score=managed_position.corroboration_score,
                supporting_sources=managed_position.supporting_sources,
                theme=managed_position.theme,
            )

        return Signal(
            instrument=context.instrument,
            action=SignalAction.HOLD,
            price=latest_bar.close,
            reason="Short-posisjon holdes videre.",
            event_id=managed_position.event_id,
            source=managed_position.source,
            stop_price=effective_stop,
            highest_price=managed_position.highest_price,
            lowest_price=lowest_price,
            trailing_active=trailing_active,
            trailing_stop_price=trailing_stop_price,
            source_count=managed_position.source_count,
            corroboration_score=managed_position.corroboration_score,
            supporting_sources=managed_position.supporting_sources,
            theme=managed_position.theme,
        )

    def _evaluate_entry_event(
        self,
        context: StrategyContext,
        event: StructuredEvent,
    ) -> Signal | None:
        latest_bar = context.bars[-1]
        latest_price = _current_price(context)
        theme = self._theme_for_event(context, event)
        profile = self._profile_for_event(context, event, theme)
        if not profile.enabled:
            return None
        evaluation_time = context.live_timestamp or context.now
        event_age = (evaluation_time - event.published_at).total_seconds()
        if event_age > self._max_event_age_for_theme(theme):
            return None

        if theme == "oil_policy":
            min_trade_score = profile.min_trade_score or 0.0
            is_positive_event = (
                event.trade_score >= min_trade_score
                and event.confidence_score >= profile.min_confidence
                and event.direction_score > 0
            )
            is_negative_short_event = (
                context.instrument.asset_class is AssetClass.STOCK
                and event.trade_score >= min_trade_score
                and event.confidence_score >= profile.min_confidence
                and event.direction_score < 0
            )
        else:
            is_positive_event = (
                event.surprise_score >= profile.min_surprise
                and event.confidence_score >= profile.min_confidence
                and event.sentiment_score >= profile.min_sentiment
            )
            is_negative_short_event = (
                context.instrument.asset_class is AssetClass.STOCK
                and event.surprise_score <= -profile.min_surprise
                and event.confidence_score >= profile.min_confidence
                and event.sentiment_score <= -profile.min_sentiment
            )
        if not is_positive_event and not is_negative_short_event:
            return None
        realtime_fast_path = _is_realtime_event_source(event) and event_age <= self.realtime_window_seconds
        if event.source_count < profile.min_source_count and not realtime_fast_path:
            return None

        anchor_price = _event_anchor_price(context.bars, event)
        if anchor_price is None:
            return None
        if is_positive_event and not _passes_positive_entry_gate(
            context=context,
            event=event,
            anchor_price=anchor_price,
            confirmation_bars=profile.confirmation_bars,
            realtime_fast_path=realtime_fast_path,
        ):
            return None
        if is_negative_short_event and not _passes_negative_entry_gate(
            context=context,
            event=event,
            anchor_price=anchor_price,
            confirmation_bars=profile.confirmation_bars,
            realtime_fast_path=realtime_fast_path,
        ):
            return None
        if not realtime_fast_path and not _has_volume_confirmation(
            context.bars,
            profile.volume_multiplier,
            allow_sparse_volume_confirmation=profile.allow_sparse_volume_confirmation,
        ):
            return None

        current_atr = atr(context.bars, 14)
        if current_atr is None:
            return None
        if is_positive_event:
            stop_price = latest_price - (1.5 * current_atr)
            if stop_price >= latest_price:
                return None

            return Signal(
                instrument=context.instrument,
                action=SignalAction.BUY,
                price=latest_price,
                reason=(
                    f"{'Oil policy-event' if theme == 'oil_policy' else 'Structured surprise-event'} "
                    f"{event.event_id} "
                    f"{'utløst i realtid uten bar-delay.' if realtime_fast_path else f'bekreftet av {event.source_count} kilder, {profile.confirmation_bars} bar(er) og volum.'}"
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
                source_count=event.source_count,
                corroboration_score=event.corroboration_score,
                supporting_sources=event.supporting_sources,
                target_leverage=1.0 if theme == "oil_policy" else self.target_leverage,
                highest_price=max(latest_bar.high, latest_price),
                lowest_price=min(latest_bar.low, latest_price),
                trailing_active=False,
                trailing_stop_price=None,
                risk_per_trade_override=profile.risk_per_trade_override,
                theme=theme,
                topic_tags=event.topic_tags,
                entity_tags=event.entity_tags,
                direction_score=event.direction_score,
                magnitude_score=event.magnitude_score,
                unexpectedness_score=event.unexpectedness_score,
                trade_score=event.trade_score,
            )

        stop_price = latest_price + (1.5 * current_atr)
        if stop_price <= latest_price:
            return None
        return Signal(
            instrument=context.instrument,
            action=SignalAction.SHORT,
            price=latest_price,
            reason=(
                f"{'Bearish oil policy-event' if theme == 'oil_policy' else 'Negativt structured surprise-event'} "
                f"{event.event_id} "
                f"{'utløst i realtid uten bar-delay.' if realtime_fast_path else f'bekreftet av {event.source_count} kilder, {profile.confirmation_bars} bar(er) og volum.'}"
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
            source_count=event.source_count,
            corroboration_score=event.corroboration_score,
            supporting_sources=event.supporting_sources,
            target_leverage=1.0 if theme == "oil_policy" else self.target_leverage,
            highest_price=max(latest_bar.high, latest_price),
            lowest_price=min(latest_bar.low, latest_price),
            trailing_active=False,
            trailing_stop_price=None,
            risk_per_trade_override=profile.risk_per_trade_override,
            theme=theme,
            topic_tags=event.topic_tags,
            entity_tags=event.entity_tags,
            direction_score=event.direction_score,
            magnitude_score=event.magnitude_score,
            unexpectedness_score=event.unexpectedness_score,
            trade_score=event.trade_score,
        )

    def _theme_for_event(self, context: StrategyContext, event: StructuredEvent) -> str:
        if event.theme == "oil_policy" or context.instrument.symbol.upper() in self.oil_proxy_symbols:
            return "oil_policy"
        if context.instrument.asset_class is AssetClass.CRYPTO:
            return "btc_news"
        if context.instrument.symbol.upper() == "TSLA":
            return "tsla_news"
        return event.theme or "general_news"

    def _profile_for_event(
        self,
        context: StrategyContext,
        event: StructuredEvent,
        theme: str,
    ) -> NewsShockProfile:
        if theme == "oil_policy":
            base = self.oil_profile
        elif context.instrument.asset_class is AssetClass.CRYPTO:
            base = self.btc_profile
        else:
            base = self.default_profile

        setting = self.strategy_settings.get(theme)
        if setting is None:
            return base
        return replace(
            base,
            enabled=setting.enabled,
            min_surprise=setting.min_surprise if setting.min_surprise is not None else base.min_surprise,
            min_confidence=setting.min_confidence if setting.min_confidence is not None else base.min_confidence,
            min_sentiment=setting.min_sentiment if setting.min_sentiment is not None else base.min_sentiment,
            min_source_count=setting.min_source_count if setting.min_source_count is not None else base.min_source_count,
            confirmation_bars=setting.confirmation_bars if setting.confirmation_bars is not None else base.confirmation_bars,
            volume_multiplier=setting.volume_multiplier if setting.volume_multiplier is not None else base.volume_multiplier,
            min_trade_score=setting.min_trade_score if setting.min_trade_score is not None else base.min_trade_score,
            risk_per_trade_override=setting.risk_per_trade if setting.risk_per_trade is not None else base.risk_per_trade_override,
        )

    def _max_event_age_for_theme(self, theme: str) -> int:
        setting = self.strategy_settings.get(theme)
        if setting is not None and setting.max_event_age_seconds is not None:
            return setting.max_event_age_seconds
        return self.max_event_age_seconds


class EarningsSurpriseStrategy:
    def __init__(
        self,
        *,
        min_eps_surprise_pct: float,
        min_revenue_surprise_pct: float,
        max_event_age_seconds: int,
        confirmation_bars: int,
        volume_multiplier: float,
        min_risk_multiplier: float,
        max_risk_multiplier: float,
        flatten_minutes_before_close: int = 10,
        strategy_settings: dict[str, StrategySetting] | None = None,
    ) -> None:
        self.min_eps_surprise_pct = min_eps_surprise_pct
        self.min_revenue_surprise_pct = min_revenue_surprise_pct
        self.max_event_age_seconds = max_event_age_seconds
        self.confirmation_bars = confirmation_bars
        self.volume_multiplier = volume_multiplier
        self.min_risk_multiplier = min_risk_multiplier
        self.max_risk_multiplier = max_risk_multiplier
        self.flatten_minutes_before_close = flatten_minutes_before_close
        self.strategy_settings = strategy_settings or {}
        self.minimum_bars_required = max(21, 15, confirmation_bars + 2, 5)

    def evaluate(self, context: StrategyContext) -> Signal:
        latest_bar = context.bars[-1]
        settings = self.strategy_settings.get("earnings_surprise")
        if settings is not None and not settings.enabled and context.managed_position is None:
            return Signal(
                instrument=context.instrument,
                action=SignalAction.HOLD,
                price=latest_bar.close,
                reason="earnings_surprise er deaktivert i dashboard-innstillingene.",
                theme="earnings_surprise",
            )

        if context.managed_position is not None and context.position_qty > 0:
            exit_signal = self._evaluate_open_position(context)
            if exit_signal is not None:
                return exit_signal

        for release in context.earnings_releases:
            if context.position_qty > 0:
                break
            signal = self._evaluate_entry_release(context, release)
            if signal is not None:
                return signal

        return Signal(
            instrument=context.instrument,
            action=SignalAction.HOLD,
            price=latest_bar.close,
            reason="Ingen earnings-release passerte entry-filtrene.",
        )

    def _evaluate_entry_release(
        self,
        context: StrategyContext,
        release: EarningsRelease,
    ) -> Signal | None:
        settings = self.strategy_settings.get("earnings_surprise")
        min_eps_surprise_pct = (
            settings.min_surprise if settings is not None and settings.min_surprise is not None else self.min_eps_surprise_pct
        )
        confirmation_bars = (
            settings.confirmation_bars if settings is not None and settings.confirmation_bars is not None else self.confirmation_bars
        )
        volume_multiplier = (
            settings.volume_multiplier if settings is not None and settings.volume_multiplier is not None else self.volume_multiplier
        )
        max_event_age_seconds = (
            settings.max_event_age_seconds
            if settings is not None and settings.max_event_age_seconds is not None
            else self.max_event_age_seconds
        )
        min_risk_multiplier = (
            settings.risk_multiplier_min
            if settings is not None and settings.risk_multiplier_min is not None
            else self.min_risk_multiplier
        )
        max_risk_multiplier = (
            settings.risk_multiplier_max
            if settings is not None and settings.risk_multiplier_max is not None
            else self.max_risk_multiplier
        )
        if not release.in_universe or not release.extended_hours_eligible:
            return None
        event_age = (context.now - release.observed_at).total_seconds()
        if event_age > max_event_age_seconds:
            return None
        if release.eps_surprise_pct < min_eps_surprise_pct:
            return None
        if release.revenue_surprise_pct < self.min_revenue_surprise_pct:
            return None
        anchor_price = release.anchor_price
        if anchor_price is None:
            anchor_price = _anchor_price_at_timestamp(context.bars, release.observed_at)
        if anchor_price is None:
            return None
        if not _has_confirmation_bars_after_timestamp(
            context.bars,
            timestamp=release.observed_at,
            anchor_price=anchor_price,
            confirmation_bars=confirmation_bars,
        ):
            return None
        if not _has_earnings_volume_confirmation(
            context.bars,
            volume_multiplier=volume_multiplier,
        ):
            return None
        current_atr = atr(context.bars, 14)
        if current_atr is None:
            return None
        latest_bar = context.bars[-1]
        stop_price = latest_bar.close - (2.0 * current_atr)
        if stop_price >= latest_bar.close:
            return None
        risk_multiplier = _earnings_risk_multiplier(
            release=release,
            min_eps_surprise_pct=min_eps_surprise_pct,
            min_revenue_surprise_pct=self.min_revenue_surprise_pct,
            min_multiplier=min_risk_multiplier,
            max_multiplier=max_risk_multiplier,
        )
        return Signal(
            instrument=context.instrument,
            action=SignalAction.BUY,
            price=latest_bar.close,
            reason=(
                f"Earnings beat: EPS {release.eps_actual:.2f} vs {release.eps_estimate:.2f} "
                f"({release.eps_surprise_pct * 100:.1f}%), revenue {release.revenue_actual:.0f} vs "
                f"{release.revenue_estimate:.0f} ({release.revenue_surprise_pct * 100:.1f}%). "
                f"Risikofaktor {risk_multiplier:.2f}x."
            ),
            event_id=release.event_id,
            source=release.source,
            anchor_price=anchor_price,
            stop_price=stop_price,
            actual_value=release.eps_actual,
            expected_value=release.eps_estimate,
            surprise_score=release.eps_surprise_pct,
            highest_price=latest_bar.high,
            lowest_price=latest_bar.low,
            trailing_active=False,
            trailing_stop_price=None,
            risk_multiplier=risk_multiplier,
            risk_per_trade_override=settings.risk_per_trade if settings is not None else None,
            theme="earnings_surprise",
        )

    def _evaluate_open_position(self, context: StrategyContext) -> Signal | None:
        managed_position = context.managed_position
        latest_bar = context.bars[-1]
        current_atr = atr(context.bars, 14)
        if current_atr is None:
            return None
        highest_price = max(managed_position.highest_price, latest_bar.high)
        profit_pct = (latest_bar.close / managed_position.entry_price) - 1
        trailing_active = managed_position.trailing_active or profit_pct >= 0.03
        trailing_stop_price = managed_position.trailing_stop_price
        if trailing_active:
            trailing_stop_price = highest_price - (2.0 * current_atr)
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
                theme=managed_position.theme or "earnings_surprise",
            )

        if (
            profit_pct >= 0.0
            and _has_momentum_faded(
                context.bars,
                highest_price=highest_price,
                fade_bars=3,
                min_pullback_pct=0.0075,
            )
        ):
            return Signal(
                instrument=context.instrument,
                action=SignalAction.SELL,
                price=latest_bar.close,
                reason="Stenger earnings-posisjon når stigningen avtar.",
                event_id=managed_position.event_id,
                source=managed_position.source,
                exit_reason="momentum_fade",
                stop_price=effective_stop,
                highest_price=highest_price,
                trailing_active=trailing_active,
                trailing_stop_price=trailing_stop_price,
                theme=managed_position.theme or "earnings_surprise",
            )

        if _is_near_us_market_close(context.now, self.flatten_minutes_before_close):
            return Signal(
                instrument=context.instrument,
                action=SignalAction.SELL,
                price=latest_bar.close,
                reason="Stenger earnings-posisjon før relevant close.",
                event_id=managed_position.event_id,
                source=managed_position.source,
                exit_reason="pre_close_flatten",
                stop_price=effective_stop,
                highest_price=highest_price,
                trailing_active=trailing_active,
                trailing_stop_price=trailing_stop_price,
                theme=managed_position.theme or "earnings_surprise",
            )

        return Signal(
            instrument=context.instrument,
            action=SignalAction.HOLD,
            price=latest_bar.close,
            reason="Earnings-posisjon holdes videre.",
            event_id=managed_position.event_id,
            source=managed_position.source,
            stop_price=effective_stop,
            highest_price=highest_price,
            trailing_active=trailing_active,
            trailing_stop_price=trailing_stop_price,
            theme=managed_position.theme or "earnings_surprise",
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


def _current_price(context: StrategyContext) -> float:
    if context.live_price is not None:
        return context.live_price
    return context.bars[-1].close


def _anchor_price_at_timestamp(bars: list[Bar], timestamp: datetime) -> float | None:
    eligible = [bar.close for bar in bars if bar.timestamp <= timestamp]
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


def _passes_positive_entry_gate(
    *,
    context: StrategyContext,
    event: StructuredEvent,
    anchor_price: float,
    confirmation_bars: int,
    realtime_fast_path: bool,
) -> bool:
    if realtime_fast_path:
        return _current_price(context) > anchor_price
    return _has_confirmation_bars(context.bars, event, anchor_price, confirmation_bars)


def _has_confirmation_bars_after_timestamp(
    bars: list[Bar],
    *,
    timestamp: datetime,
    anchor_price: float,
    confirmation_bars: int,
) -> bool:
    post_event_bars = [bar for bar in bars if bar.timestamp > timestamp]
    if len(post_event_bars) < confirmation_bars:
        return False
    confirmation_slice = post_event_bars[-confirmation_bars:]
    return all(bar.close > anchor_price for bar in confirmation_slice)


def _has_short_confirmation_bars(
    bars: list[Bar],
    event: StructuredEvent,
    anchor_price: float,
    confirmation_bars: int,
) -> bool:
    post_event_bars = [bar for bar in bars if bar.timestamp > event.published_at]
    if len(post_event_bars) < confirmation_bars:
        return False
    confirmation_slice = post_event_bars[-confirmation_bars:]
    return all(bar.close < anchor_price for bar in confirmation_slice)


def _passes_negative_entry_gate(
    *,
    context: StrategyContext,
    event: StructuredEvent,
    anchor_price: float,
    confirmation_bars: int,
    realtime_fast_path: bool,
) -> bool:
    if realtime_fast_path:
        return _current_price(context) < anchor_price
    return _has_short_confirmation_bars(context.bars, event, anchor_price, confirmation_bars)


def _is_realtime_event_source(event: StructuredEvent) -> bool:
    normalized = event.source.strip().lower()
    return (
        normalized == "finnhub_webhook"
        or normalized.startswith("x_webhook:")
        or normalized.startswith("x_stream:")
    )


def _has_volume_confirmation(
    bars: list[Bar],
    volume_multiplier: float,
    *,
    allow_sparse_volume_confirmation: bool,
) -> bool:
    if len(bars) < 21:
        return False
    latest_bar = bars[-1]
    previous_bars = bars[-21:-1]
    positive_volumes = [bar.volume for bar in previous_bars if bar.volume > 0]
    if allow_sparse_volume_confirmation and len(positive_volumes) < max(3, len(previous_bars) // 4):
        return True
    average_volume = fmean(positive_volumes) if positive_volumes else fmean(bar.volume for bar in previous_bars)
    if average_volume <= 0:
        return False
    return latest_bar.volume >= average_volume * volume_multiplier


def _has_earnings_volume_confirmation(
    bars: list[Bar],
    *,
    volume_multiplier: float,
) -> bool:
    if len(bars) < 21:
        return False
    latest_bar = bars[-1]
    previous_bars = bars[-21:-1]
    positive_volumes = [bar.volume for bar in previous_bars if bar.volume > 0]
    if len(positive_volumes) < 5:
        return True
    average_volume = fmean(positive_volumes)
    if average_volume <= 0:
        return True
    return latest_bar.volume >= average_volume * volume_multiplier


def _earnings_risk_multiplier(
    *,
    release: EarningsRelease,
    min_eps_surprise_pct: float,
    min_revenue_surprise_pct: float,
    min_multiplier: float,
    max_multiplier: float,
) -> float:
    eps_strength = release.eps_surprise_pct / min_eps_surprise_pct if min_eps_surprise_pct > 0 else 1.0
    revenue_strength = (
        release.revenue_surprise_pct / min_revenue_surprise_pct if min_revenue_surprise_pct > 0 else 1.0
    )
    weighted_strength = (0.65 * eps_strength) + (0.35 * revenue_strength)
    clamped = max(min_multiplier, min(max_multiplier, weighted_strength))
    return round(clamped, 2)


def _has_momentum_faded(
    bars: list[Bar],
    *,
    highest_price: float,
    fade_bars: int,
    min_pullback_pct: float,
) -> bool:
    if len(bars) < fade_bars or highest_price <= 0:
        return False
    recent_bars = bars[-fade_bars:]
    descending_closes = all(
        current.close < previous.close
        for previous, current in zip(recent_bars, recent_bars[1:])
    )
    if not descending_closes:
        return False
    drawdown_from_high = (highest_price - recent_bars[-1].close) / highest_price
    return drawdown_from_high >= min_pullback_pct


def _is_near_us_market_close(now: datetime, flatten_minutes_before_close: int) -> bool:
    if not isinstance(now, datetime):
        return False
    eastern = ZoneInfo("America/New_York")
    now_eastern = now.astimezone(eastern)
    close_minutes = (16 * 60) - flatten_minutes_before_close
    current_minutes = now_eastern.hour * 60 + now_eastern.minute
    return now_eastern.weekday() < 5 and current_minutes >= close_minutes
