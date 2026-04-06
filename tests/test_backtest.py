from datetime import datetime, timedelta, timezone

from trading_bot.backtest import run_backtest
from trading_bot.domain import AssetClass, Bar, Instrument, OrderSide, StructuredEvent, StructuredEventCategory
from trading_bot.risk import RiskManager
from trading_bot.strategy import NewsShockStrategy


def make_bar(close: float, minute_offset: int, volume: float = 100, high: float | None = None, low: float | None = None) -> Bar:
    base_time = datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc)
    return Bar(
        timestamp=base_time + timedelta(minutes=minute_offset),
        open=close,
        high=high if high is not None else close + 0.2,
        low=low if low is not None else close - 0.2,
        close=close,
        volume=volume,
    )


def test_backtest_replays_structured_events_and_flattens_stock_position() -> None:
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
    )
    risk_manager = RiskManager(0.005, 0.20, 2, 50)
    bars = [make_bar(100 + (index * 0.1), index, volume=100) for index in range(20)]
    bars.extend(
        [
            make_bar(103, 20, volume=140),
            make_bar(104, 21, volume=170),
            make_bar(105, 22, volume=400),
            make_bar(106, 23, volume=250),
            make_bar(100, 24, volume=260, high=101, low=99),
        ]
    )
    event = StructuredEvent(
        event_id="evt-1",
        source="finnhub",
        instrument_scope=("TSLA",),
        category=StructuredEventCategory.EARNINGS,
        published_at=bars[20].timestamp - timedelta(seconds=30),
        headline="TSLA beat",
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

    result = run_backtest(
        instrument=Instrument("TSLA", AssetClass.STOCK),
        bars=bars,
        strategy=strategy,
        risk_manager=risk_manager,
        initial_cash=10_000,
        events=(event,),
    )

    assert len(result.trades) >= 2
    assert result.ending_position_qty == 0


def test_backtest_replays_negative_tsla_news_as_short_then_cover() -> None:
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
    )
    risk_manager = RiskManager(0.005, 0.20, 2, 50)
    bars = [make_bar(120 - (index * 0.2), index, volume=120) for index in range(20)]
    bars.extend(
        [
            make_bar(116, 20, volume=160),
            make_bar(115, 21, volume=180),
            make_bar(114, 22, volume=430),
            make_bar(110, 23, volume=300, high=111, low=109),
            make_bar(116, 24, volume=350, high=117, low=115),
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

    result = run_backtest(
        instrument=Instrument("TSLA", AssetClass.STOCK),
        bars=bars,
        strategy=strategy,
        risk_manager=risk_manager,
        initial_cash=10_000,
        events=(event,),
    )

    assert len(result.trades) >= 2
    assert result.trades[0].side is OrderSide.SELL
    assert result.trades[1].side is OrderSide.BUY
    assert result.ending_position_qty == 0
