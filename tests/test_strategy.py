from datetime import datetime, timedelta, timezone

from trading_bot.domain import (
    AssetClass,
    Bar,
    Instrument,
    ManagedPosition,
    SignalAction,
    StrategySetting,
    StrategyContext,
    StructuredEvent,
    StructuredEventCategory,
)
from trading_bot.strategy import NewsShockStrategy


def make_bar(close: float, minute_offset: int, volume: float = 100, high: float | None = None, low: float | None = None) -> Bar:
    base_time = datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc)
    return Bar(
        timestamp=base_time + timedelta(minutes=minute_offset),
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=volume,
    )


def build_strategy() -> NewsShockStrategy:
    return NewsShockStrategy(
        min_surprise=0.75,
        min_confidence=0.80,
        min_sentiment=0.20,
        min_source_count=2,
        confirmation_bars=2,
        volume_multiplier=1.5,
        max_event_age_seconds=300,
        realtime_window_seconds=20,
        btc_max_hold_minutes=360,
        stock_flatten_minutes_before_close=10,
        target_leverage=10,
        btc_min_surprise=0.15,
        btc_min_confidence=0.55,
        btc_min_sentiment=0.08,
        btc_min_source_count=1,
        btc_confirmation_bars=1,
        btc_volume_multiplier=1.05,
        btc_momentum_fade_bars=3,
        btc_momentum_fade_min_profit_pct=0.003,
        btc_momentum_fade_from_high_pct=0.0015,
        oil_proxy_symbols=("USO", "XLE", "OXY", "XOM", "CVX", "SLB"),
        oil_min_trade_score=0.65,
        oil_min_confidence=0.70,
        oil_confirmation_bars=1,
        oil_volume_multiplier=1.1,
        oil_risk_per_trade=0.004,
    )


def test_news_shock_buys_on_confirmed_positive_surprise() -> None:
    strategy = build_strategy()
    bars = [make_bar(100 + (index * 0.2), index, volume=100) for index in range(20)]
    bars.extend(
        [
            make_bar(103, 20, volume=140),
            make_bar(104, 21, volume=170),
            make_bar(105, 22, volume=400, high=106, low=104),
        ]
    )
    event = StructuredEvent(
        event_id="evt-1",
        source="finnhub",
        instrument_scope=("TSLA",),
        category=StructuredEventCategory.EARNINGS,
        published_at=bars[20].timestamp - timedelta(seconds=30),
        headline="TSLA beats expectations",
        actual_value=1.5,
        expected_value=1.2,
        surprise_score=0.8,
        sentiment_score=0.3,
        confidence_score=0.9,
        is_scheduled=True,
        supporting_sources=("finnhub", "sec_press"),
        source_count=2,
        corroboration_score=2.0,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("TSLA", AssetClass.STOCK),
            bars=bars,
            position_qty=0,
            now=bars[-1].timestamp,
            structured_events=(event,),
        )
    )

    assert signal.action is SignalAction.BUY
    assert signal.event_id == "evt-1"
    assert signal.stop_price is not None


def test_news_shock_buys_immediately_on_realtime_x_webhook_without_waiting_for_next_bar() -> None:
    strategy = build_strategy()
    bars = [make_bar(100 + (index * 0.1), index, volume=100) for index in range(24)]
    event_time = bars[-1].timestamp + timedelta(seconds=5)
    event = StructuredEvent(
        event_id="x-webhook-evt-1",
        source="x_webhook:@elonmusk",
        instrument_scope=("TSLA",),
        category=StructuredEventCategory.OTHER,
        published_at=event_time,
        headline="Tesla robotaxi approval may come sooner than expected",
        actual_value=None,
        expected_value=None,
        surprise_score=0.9,
        sentiment_score=0.35,
        confidence_score=0.92,
        is_scheduled=False,
        supporting_sources=("x_webhook:@elonmusk",),
        source_count=1,
        corroboration_score=1.0,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("TSLA", AssetClass.STOCK),
            bars=bars,
            position_qty=0,
            now=event_time,
            live_price=103.5,
            live_timestamp=event_time,
            structured_events=(event,),
        )
    )

    assert signal.action is SignalAction.BUY
    assert "realtid" in signal.reason


def test_news_shock_buys_immediately_on_realtime_x_stream_without_waiting_for_next_bar() -> None:
    strategy = build_strategy()
    bars = [make_bar(100 + (index * 0.1), index, volume=100) for index in range(24)]
    event_time = bars[-1].timestamp + timedelta(seconds=5)
    event = StructuredEvent(
        event_id="x-stream-evt-1",
        source="x_stream:@elonmusk",
        instrument_scope=("TSLA",),
        category=StructuredEventCategory.OTHER,
        published_at=event_time,
        headline="Tesla approval may come sooner than expected",
        actual_value=None,
        expected_value=None,
        surprise_score=0.9,
        sentiment_score=0.35,
        confidence_score=0.92,
        is_scheduled=False,
        supporting_sources=("x_stream:@elonmusk",),
        source_count=1,
        corroboration_score=1.0,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("TSLA", AssetClass.STOCK),
            bars=bars,
            position_qty=0,
            now=event_time,
            live_price=103.5,
            live_timestamp=event_time,
            structured_events=(event,),
        )
    )

    assert signal.action is SignalAction.BUY
    assert "realtid" in signal.reason


def test_news_shock_holds_on_old_event() -> None:
    strategy = build_strategy()
    bars = [make_bar(100 + index, index, volume=200) for index in range(25)]
    event = StructuredEvent(
        event_id="evt-old",
        source="finnhub",
        instrument_scope=("TSLA",),
        category=StructuredEventCategory.EARNINGS,
        published_at=bars[-1].timestamp - timedelta(minutes=10),
        headline="TSLA old beat",
        actual_value=1.5,
        expected_value=1.2,
        surprise_score=0.9,
        sentiment_score=0.4,
        confidence_score=0.95,
        is_scheduled=True,
        supporting_sources=("finnhub", "sec_press"),
        source_count=2,
        corroboration_score=2.0,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("TSLA", AssetClass.STOCK),
            bars=bars,
            position_qty=0,
            now=bars[-1].timestamp,
            structured_events=(event,),
        )
    )

    assert signal.action is SignalAction.HOLD


def test_news_shock_buys_on_bullish_oil_policy_event() -> None:
    strategy = build_strategy()
    bars = [make_bar(80 + (index * 0.1), index, volume=150) for index in range(21)]
    bars.append(make_bar(82.6, 21, volume=260, high=83.0, low=82.1))
    event = StructuredEvent(
        event_id="evt-oil",
        source="white_house",
        instrument_scope=("USO", "XLE"),
        category=StructuredEventCategory.ENERGY_POLICY,
        published_at=bars[-2].timestamp - timedelta(seconds=20),
        headline="Trump signals sanctions on major crude exporter after White House energy review",
        actual_value=None,
        expected_value=None,
        surprise_score=0.6,
        sentiment_score=0.4,
        confidence_score=0.82,
        is_scheduled=False,
        supporting_sources=("white_house",),
        source_count=1,
        corroboration_score=1.0,
        theme="oil_policy",
        topic_tags=("oil", "sanctions"),
        entity_tags=("trump", "white_house"),
        direction_score=0.7,
        magnitude_score=0.8,
        unexpectedness_score=0.8,
        trade_score=0.78,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("USO", AssetClass.STOCK),
            bars=bars,
            position_qty=0,
            now=bars[-1].timestamp,
            structured_events=(event,),
        )
    )

    assert signal.action is SignalAction.BUY
    assert signal.theme == "oil_policy"
    assert signal.risk_per_trade_override == 0.004


def test_news_shock_respects_disabled_oil_policy_setting() -> None:
    strategy = NewsShockStrategy(
        min_surprise=0.75,
        min_confidence=0.80,
        min_sentiment=0.20,
        min_source_count=2,
        confirmation_bars=2,
        volume_multiplier=1.5,
        max_event_age_seconds=300,
        realtime_window_seconds=20,
        btc_max_hold_minutes=360,
        stock_flatten_minutes_before_close=10,
        target_leverage=10,
        btc_min_surprise=0.15,
        btc_min_confidence=0.55,
        btc_min_sentiment=0.08,
        btc_min_source_count=1,
        btc_confirmation_bars=1,
        btc_volume_multiplier=1.05,
        btc_momentum_fade_bars=3,
        btc_momentum_fade_min_profit_pct=0.003,
        btc_momentum_fade_from_high_pct=0.0015,
        oil_proxy_symbols=("USO", "XLE", "OXY", "XOM", "CVX", "SLB"),
        oil_min_trade_score=0.65,
        oil_min_confidence=0.70,
        oil_confirmation_bars=1,
        oil_volume_multiplier=1.1,
        oil_risk_per_trade=0.004,
        strategy_settings={"oil_policy": StrategySetting(theme="oil_policy", enabled=False)},
    )
    bars = [make_bar(80 + (index * 0.1), index, volume=150) for index in range(21)]
    bars.append(make_bar(82.6, 21, volume=260, high=83.0, low=82.1))
    event = StructuredEvent(
        event_id="evt-oil-disabled",
        source="white_house",
        instrument_scope=("USO",),
        category=StructuredEventCategory.ENERGY_POLICY,
        published_at=bars[-2].timestamp - timedelta(seconds=20),
        headline="Trump executive order creates unexpected oil supply shock",
        actual_value=None,
        expected_value=None,
        surprise_score=0.6,
        sentiment_score=0.4,
        confidence_score=0.82,
        is_scheduled=False,
        supporting_sources=("white_house",),
        source_count=1,
        corroboration_score=1.0,
        theme="oil_policy",
        direction_score=0.7,
        magnitude_score=0.8,
        unexpectedness_score=0.8,
        trade_score=0.78,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("USO", AssetClass.STOCK),
            bars=bars,
            position_qty=0,
            now=bars[-1].timestamp,
            structured_events=(event,),
        )
    )

    assert signal.action is SignalAction.HOLD


def test_news_shock_holds_on_low_confidence_event() -> None:
    strategy = build_strategy()
    bars = [make_bar(100 + index, index, volume=200) for index in range(25)]
    event = StructuredEvent(
        event_id="evt-low-confidence",
        source="finnhub",
        instrument_scope=("TSLA",),
        category=StructuredEventCategory.EARNINGS,
        published_at=bars[-1].timestamp - timedelta(seconds=30),
        headline="TSLA low confidence beat",
        actual_value=1.5,
        expected_value=1.2,
        surprise_score=0.9,
        sentiment_score=0.4,
        confidence_score=0.5,
        is_scheduled=True,
        supporting_sources=("finnhub", "sec_press"),
        source_count=2,
        corroboration_score=2.0,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("TSLA", AssetClass.STOCK),
            bars=bars,
            position_qty=0,
            now=bars[-1].timestamp,
            structured_events=(event,),
        )
    )

    assert signal.action is SignalAction.HOLD


def test_news_shock_sells_when_trailing_stop_is_hit() -> None:
    strategy = build_strategy()
    bars = [make_bar(100 + (index * 0.3), index, volume=200, high=100 + (index * 0.3) + 0.5, low=99 + (index * 0.3)) for index in range(25)]
    bars.append(make_bar(104, 25, volume=300, high=110, low=103))
    bars.append(make_bar(101, 26, volume=260, high=101.5, low=100))
    managed_position = ManagedPosition(
        instrument=Instrument("TSLA", AssetClass.STOCK),
        qty=1,
        entry_price=100,
        entry_time=bars[10].timestamp,
        highest_price=108,
        lowest_price=100,
        stop_price=98,
        initial_stop_price=98,
        trailing_active=True,
        trailing_stop_price=102,
        event_id="evt-1",
        source="finnhub",
        anchor_price=100,
        actual_value=1.5,
        expected_value=1.2,
        surprise_score=0.8,
        sentiment_score=0.3,
        confidence_score=0.9,
        source_count=2,
        corroboration_score=2.0,
        supporting_sources=("finnhub", "sec_press"),
        target_leverage=10,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("TSLA", AssetClass.STOCK),
            bars=bars,
            position_qty=1,
            now=bars[-1].timestamp,
            managed_position=managed_position,
        )
    )

    assert signal.action is SignalAction.SELL
    assert signal.exit_reason in {"trailing_stop", "hard_stop"}


def test_news_shock_exits_btc_after_max_hold_time() -> None:
    strategy = build_strategy()
    bars = [make_bar(50_000 + index, index, volume=500) for index in range(25)]
    managed_position = ManagedPosition(
        instrument=Instrument("BTC/USD", AssetClass.CRYPTO),
        qty=0.1,
        entry_price=50_000,
        entry_time=bars[0].timestamp - timedelta(hours=7),
        highest_price=50_500,
        lowest_price=50_000,
        stop_price=49_000,
        initial_stop_price=49_000,
        trailing_active=False,
        trailing_stop_price=None,
        event_id="evt-btc",
        source="finnhub",
        anchor_price=50_000,
        actual_value=3.2,
        expected_value=2.8,
        surprise_score=0.15,
        sentiment_score=0.3,
        confidence_score=0.85,
        source_count=2,
        corroboration_score=2.0,
        supporting_sources=("finnhub", "fed_monetary"),
        target_leverage=10,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("BTC/USD", AssetClass.CRYPTO),
            bars=bars,
            position_qty=0.1,
            now=bars[-1].timestamp,
            managed_position=managed_position,
        )
    )

    assert signal.action is SignalAction.SELL
    assert signal.exit_reason == "max_hold_time"


def test_news_shock_holds_stock_on_single_source_event() -> None:
    strategy = build_strategy()
    bars = [make_bar(100 + (index * 0.2), index, volume=100) for index in range(20)]
    bars.extend(
        [
            make_bar(103, 20, volume=140),
            make_bar(104, 21, volume=170),
            make_bar(105, 22, volume=400, high=106, low=104),
        ]
    )
    event = StructuredEvent(
        event_id="evt-single-source",
        source="finnhub",
        instrument_scope=("TSLA",),
        category=StructuredEventCategory.EARNINGS,
        published_at=bars[20].timestamp - timedelta(seconds=30),
        headline="TSLA beat without corroboration",
        actual_value=1.5,
        expected_value=1.2,
        surprise_score=1.0,
        sentiment_score=0.6,
        confidence_score=0.9,
        is_scheduled=True,
        supporting_sources=("finnhub",),
        source_count=1,
        corroboration_score=1.0,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("TSLA", AssetClass.STOCK),
            bars=bars,
            position_qty=0,
            now=bars[-1].timestamp,
            structured_events=(event,),
        )
    )

    assert signal.action is SignalAction.HOLD


def test_news_shock_buys_btc_on_small_positive_unexpected_news() -> None:
    strategy = build_strategy()
    bars = [make_bar(60_000 + (index * 5), index, volume=0.0) for index in range(22)]
    bars.append(make_bar(60_120, 22, volume=0.0, high=60_150, low=60_090))
    event = StructuredEvent(
        event_id="evt-btc-small",
        source="finnhub-webhook",
        instrument_scope=("BTC/USD",),
        category=StructuredEventCategory.OTHER,
        published_at=bars[21].timestamp - timedelta(seconds=20),
        headline="Unexpected positive BTC regulatory update",
        actual_value=None,
        expected_value=None,
        surprise_score=0.18,
        sentiment_score=0.12,
        confidence_score=0.62,
        is_scheduled=False,
        supporting_sources=("finnhub-webhook",),
        source_count=1,
        corroboration_score=1.0,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("BTC/USD", AssetClass.CRYPTO),
            bars=bars,
            position_qty=0,
            now=bars[-1].timestamp,
            structured_events=(event,),
        )
    )

    assert signal.action is SignalAction.BUY
    assert signal.event_id == "evt-btc-small"


def test_news_shock_sells_btc_when_momentum_fades() -> None:
    strategy = build_strategy()
    bars = [make_bar(60_000 + (index * 8), index, volume=120) for index in range(23)]
    bars.extend(
        [
            make_bar(60_240, 23, volume=180, high=60_300, low=60_150),
            make_bar(60_215, 24, volume=160, high=60_250, low=60_130),
            make_bar(60_190, 25, volume=150, high=60_220, low=60_110),
        ]
    )
    managed_position = ManagedPosition(
        instrument=Instrument("BTC/USD", AssetClass.CRYPTO),
        qty=0.1,
        entry_price=60_000,
        entry_time=bars[10].timestamp,
        highest_price=60_300,
        lowest_price=60_000,
        stop_price=59_700,
        initial_stop_price=59_700,
        trailing_active=False,
        trailing_stop_price=None,
        event_id="evt-btc-fade",
        source="finnhub-webhook",
        anchor_price=60_020,
        actual_value=None,
        expected_value=None,
        surprise_score=0.2,
        sentiment_score=0.15,
        confidence_score=0.65,
        source_count=1,
        corroboration_score=1.0,
        supporting_sources=("finnhub-webhook",),
        target_leverage=10,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("BTC/USD", AssetClass.CRYPTO),
            bars=bars,
            position_qty=0.1,
            now=bars[-1].timestamp,
            managed_position=managed_position,
        )
    )

    assert signal.action is SignalAction.SELL
    assert signal.exit_reason == "momentum_fade"


def test_news_shock_shorts_tsla_on_negative_surprise() -> None:
    strategy = build_strategy()
    bars = [make_bar(120 - (index * 0.2), index, volume=120) for index in range(20)]
    bars.extend(
        [
            make_bar(116, 20, volume=150),
            make_bar(115, 21, volume=180),
            make_bar(114, 22, volume=420, high=115, low=113),
        ]
    )
    event = StructuredEvent(
        event_id="evt-tsla-short",
        source="finnhub",
        instrument_scope=("TSLA",),
        category=StructuredEventCategory.EARNINGS,
        published_at=bars[20].timestamp - timedelta(seconds=20),
        headline="TSLA misses expectations badly",
        actual_value=0.9,
        expected_value=1.2,
        surprise_score=-0.82,
        sentiment_score=-0.35,
        confidence_score=0.92,
        is_scheduled=True,
        supporting_sources=("finnhub", "sec_press"),
        source_count=2,
        corroboration_score=2.0,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("TSLA", AssetClass.STOCK),
            bars=bars,
            position_qty=0,
            now=bars[-1].timestamp,
            structured_events=(event,),
        )
    )

    assert signal.action is SignalAction.SHORT
    assert signal.stop_price is not None
    assert signal.stop_price > signal.price


def test_news_shock_covers_tsla_short_when_trailing_stop_hits() -> None:
    strategy = build_strategy()
    bars = [make_bar(120 - (index * 0.4), index, volume=200, high=120 - (index * 0.4) + 0.5, low=119 - (index * 0.4)) for index in range(25)]
    bars.append(make_bar(108, 25, volume=260, high=108.5, low=104))
    bars.append(make_bar(111, 26, volume=240, high=111.5, low=110))
    managed_position = ManagedPosition(
        instrument=Instrument("TSLA", AssetClass.STOCK),
        qty=-1,
        entry_price=120,
        entry_time=bars[10].timestamp,
        highest_price=120,
        lowest_price=104,
        stop_price=116,
        initial_stop_price=116,
        trailing_active=True,
        trailing_stop_price=107,
        event_id="evt-tsla-short",
        source="finnhub",
        anchor_price=118,
        actual_value=0.9,
        expected_value=1.2,
        surprise_score=-0.82,
        sentiment_score=-0.35,
        confidence_score=0.92,
        source_count=2,
        corroboration_score=2.0,
        supporting_sources=("finnhub", "sec_press"),
        target_leverage=10,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("TSLA", AssetClass.STOCK),
            bars=bars,
            position_qty=-1,
            now=bars[-1].timestamp,
            managed_position=managed_position,
        )
    )

    assert signal.action is SignalAction.COVER
    assert signal.exit_reason in {"trailing_stop", "hard_stop"}
