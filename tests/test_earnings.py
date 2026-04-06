from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from trading_bot.domain import (
    AssetClass,
    Bar,
    EarningsRelease,
    Instrument,
    ManagedPosition,
    SignalAction,
    StrategyContext,
)
from trading_bot.earnings_provider import (
    EarningsUniverseScanner,
    FinnhubSecurity,
    _passes_universe_filters,
)
from trading_bot.notifications import TelegramNotifier
from trading_bot.strategy import EarningsSurpriseStrategy


def make_bar(
    close: float,
    minute_offset: int,
    *,
    volume: float = 100,
    high: float | None = None,
    low: float | None = None,
) -> Bar:
    base_time = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    return Bar(
        timestamp=base_time + timedelta(minutes=minute_offset),
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=volume,
    )


def build_strategy() -> EarningsSurpriseStrategy:
    return EarningsSurpriseStrategy(
        min_eps_surprise_pct=0.12,
        min_revenue_surprise_pct=0.03,
        max_event_age_seconds=300,
        confirmation_bars=2,
        volume_multiplier=1.5,
        min_risk_multiplier=1.0,
        max_risk_multiplier=2.5,
    )


def test_universe_filter_rejects_otc_and_low_liquidity() -> None:
    config = SimpleNamespace(
        earnings_min_price_usd=3.0,
        earnings_market_cap_min_usd=300_000_000,
        earnings_market_cap_max_usd=10_000_000_000,
        earnings_min_avg_dollar_volume_usd=2_000_000,
    )
    otc_security = FinnhubSecurity(
        symbol="OTCM",
        description="OTC NAME",
        mic="OTCM",
        security_type="Common Stock",
    )
    assert not _passes_universe_filters(
        security=otc_security,
        latest_price=8.0,
        market_cap_usd=500_000_000,
        avg_dollar_volume_usd=5_000_000,
        config=config,
    )

    listed_security = FinnhubSecurity(
        symbol="GOOD",
        description="GOOD COMPANY INC",
        mic="XNAS",
        security_type="Common Stock",
    )
    assert not _passes_universe_filters(
        security=listed_security,
        latest_price=8.0,
        market_cap_usd=500_000_000,
        avg_dollar_volume_usd=1_000_000,
        config=config,
    )


def test_earnings_strategy_buys_on_dual_beat_with_confirmation() -> None:
    strategy = build_strategy()
    bars = [make_bar(20 + (index * 0.1), index, volume=100) for index in range(20)]
    bars.extend(
        [
            make_bar(22.1, 20, volume=120),
            make_bar(22.6, 21, volume=180),
            make_bar(23.1, 22, volume=400, high=23.3, low=22.8),
        ]
    )
    release = EarningsRelease(
        event_id="earnings-aaa",
        symbol="AAA",
        earnings_date=date(2026, 4, 6),
        observed_at=bars[20].timestamp,
        published_at=bars[20].timestamp,
        hour="bmo",
        quarter=1,
        year=2026,
        eps_actual=1.34,
        eps_estimate=1.10,
        revenue_actual=120_000_000,
        revenue_estimate=110_000_000,
        eps_surprise_pct=0.218,
        revenue_surprise_pct=0.091,
        anchor_price=22.0,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("AAA", AssetClass.STOCK),
            bars=bars,
            position_qty=0,
            now=bars[-1].timestamp,
            earnings_releases=(release,),
        )
    )

    assert signal.action is SignalAction.BUY
    assert signal.stop_price is not None
    assert signal.event_id == "earnings-aaa"
    assert signal.risk_multiplier > 1.0


def test_earnings_strategy_holds_without_revenue_beat() -> None:
    strategy = build_strategy()
    bars = [make_bar(20 + (index * 0.1), index, volume=100) for index in range(23)]
    release = EarningsRelease(
        event_id="earnings-bbb",
        symbol="BBB",
        earnings_date=date(2026, 4, 6),
        observed_at=bars[20].timestamp,
        published_at=bars[20].timestamp,
        hour="amc",
        quarter=1,
        year=2026,
        eps_actual=1.34,
        eps_estimate=1.10,
        revenue_actual=101_000_000,
        revenue_estimate=100_000_000,
        eps_surprise_pct=0.218,
        revenue_surprise_pct=0.01,
        anchor_price=21.8,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("BBB", AssetClass.STOCK),
            bars=bars,
            position_qty=0,
            now=bars[-1].timestamp,
            earnings_releases=(release,),
        )
    )

    assert signal.action is SignalAction.HOLD


def test_earnings_strategy_sells_when_momentum_fades() -> None:
    strategy = build_strategy()
    bars = [make_bar(100 + index, index, volume=200, high=100 + index + 0.4, low=99 + index) for index in range(25)]
    bars.extend(
        [
            make_bar(122, 25, volume=320, high=123, low=121),
            make_bar(120, 26, volume=280, high=121, low=119),
            make_bar(118, 27, volume=260, high=119, low=117),
            make_bar(116, 28, volume=240, high=117, low=115),
        ]
    )
    managed_position = ManagedPosition(
        instrument=Instrument("AAA", AssetClass.STOCK),
        qty=10,
        entry_price=100,
        entry_time=bars[10].timestamp,
        highest_price=123,
        lowest_price=100,
        stop_price=95,
        initial_stop_price=95,
        trailing_active=True,
        trailing_stop_price=115,
        event_id="earnings-aaa",
        source="finnhub_calendar",
        anchor_price=100,
        actual_value=1.34,
        expected_value=1.10,
        surprise_score=0.218,
        sentiment_score=None,
        confidence_score=None,
        source_count=None,
        corroboration_score=None,
        supporting_sources=(),
        target_leverage=1.0,
    )

    signal = strategy.evaluate(
        StrategyContext(
            instrument=Instrument("AAA", AssetClass.STOCK),
            bars=bars,
            position_qty=10,
            now=bars[-1].timestamp,
            managed_position=managed_position,
        )
    )

    assert signal.action is SignalAction.SELL
    assert signal.exit_reason in {"momentum_fade", "trailing_stop"}


@dataclass(frozen=True)
class FakeAsset:
    symbol: str
    tradable: bool = True
    exchange: str = "NASDAQ"


class FakeBroker:
    def list_active_tradable_us_equities(self):
        return (FakeAsset("AAA"), FakeAsset("BBB"), FakeAsset("CCC"))

    def get_latest_stock_trades(self, symbols):
        return {
            symbol: SimpleNamespace(price={"AAA": 12.0, "BBB": 7.5, "CCC": 5.0}[symbol])
            for symbol in symbols
        }

    def get_historical_stock_bars_batch(self, symbols, *, start, end, timeframe, limit=None):
        base = datetime(2026, 3, 1, tzinfo=timezone.utc)
        result = {}
        for symbol in symbols:
            close = {"AAA": 12.0, "BBB": 7.5, "CCC": 5.0}[symbol]
            volume = {"AAA": 500_000, "BBB": 300_000, "CCC": 40_000}[symbol]
            result[symbol] = [
                Bar(
                    timestamp=base + timedelta(days=index),
                    open=close,
                    high=close * 1.03,
                    low=close * 0.97,
                    close=close,
                    volume=volume,
                )
                for index in range(35)
            ]
        return result


class FakeFinnhub:
    def fetch_earnings_calendar(self, *, from_date, to_date, symbol=None):
        upcoming = [
            {
                "symbol": "AAA",
                "date": "2026-04-09",
                "hour": "amc",
                "quarter": 1,
                "year": 2026,
                "epsEstimate": 1.2,
                "revenueEstimate": 100_000_000,
            },
            {
                "symbol": "BBB",
                "date": "2026-04-10",
                "hour": "bmo",
                "quarter": 1,
                "year": 2026,
                "epsEstimate": 0.7,
                "revenueEstimate": 80_000_000,
            },
            {
                "symbol": "CCC",
                "date": "2026-04-08",
                "hour": "bmo",
                "quarter": 1,
                "year": 2026,
                "epsEstimate": 0.2,
                "revenueEstimate": 40_000_000,
            },
        ]
        history = {
            "AAA": [
                {"epsActual": 1.3, "epsEstimate": 1.1, "revenueActual": 110_000_000, "revenueEstimate": 100_000_000},
                {"epsActual": 1.2, "epsEstimate": 1.0, "revenueActual": 105_000_000, "revenueEstimate": 100_000_000},
            ],
            "BBB": [
                {"epsActual": 0.7, "epsEstimate": 0.72, "revenueActual": 79_000_000, "revenueEstimate": 80_000_000},
            ],
            "CCC": [
                {"epsActual": 0.2, "epsEstimate": 0.21, "revenueActual": 40_000_000, "revenueEstimate": 40_000_000},
            ],
        }
        if symbol is not None:
            return history[symbol]
        return upcoming

    def fetch_us_common_stocks(self):
        return {
            "AAA": FinnhubSecurity("AAA", "AAA INC", "XNAS", "Common Stock"),
            "BBB": FinnhubSecurity("BBB", "BBB INC", "XNYS", "Common Stock"),
            "CCC": FinnhubSecurity("CCC", "CCC INC", "XNAS", "Common Stock"),
        }

    def fetch_profile(self, symbol):
        return {
            "AAA": {"marketCapitalization": 900, "exchange": "NASDAQ", "name": "AAA Inc", "finnhubIndustry": "Software"},
            "BBB": {"marketCapitalization": 700, "exchange": "NYSE", "name": "BBB Inc", "finnhubIndustry": "Retail"},
            "CCC": {"marketCapitalization": 350, "exchange": "NASDAQ", "name": "CCC Inc", "finnhubIndustry": "Energy"},
        }[symbol]

    def fetch_eps_surprise_history(self, symbol, limit=4):
        return []


class FakeSec:
    def filing_freshness_score(self, symbol, as_of):
        return {"AAA": 85.0, "BBB": 40.0, "CCC": 20.0}[symbol]


def test_scanner_limits_universe_and_sorts_highest_score_first() -> None:
    scanner = EarningsUniverseScanner(
        broker=FakeBroker(),
        finnhub_api_key="token",
        sec_user_agent="test-agent",
        database=None,
    )
    scanner.finnhub = FakeFinnhub()
    scanner.sec = FakeSec()
    config = SimpleNamespace(
        earnings_lookahead_days=7,
        earnings_universe_max_size=2,
        earnings_market_cap_min_usd=300_000_000,
        earnings_market_cap_max_usd=10_000_000_000,
        earnings_min_price_usd=3.0,
        earnings_min_avg_dollar_volume_usd=2_000_000,
    )

    analyses = scanner.scan(config, as_of=datetime(2026, 4, 6, 8, 0, tzinfo=timezone.utc))

    assert [analysis.candidate.symbol for analysis in analyses] == ["AAA", "BBB"]
    assert analyses[0].score >= analyses[1].score


def test_telegram_watchlist_message_lists_top_candidates() -> None:
    notifier = TelegramNotifier(bot_token="token", chat_id="123")
    analyses = [
        SimpleNamespace(
            candidate=SimpleNamespace(
                symbol="AAA",
                earnings_date=date(2026, 4, 9),
                earnings_hour="amc",
                eps_estimate=1.2,
                revenue_estimate=100_000_000,
                last_price=12.0,
            ),
            score=88.4,
            reasons=("score 88.4",),
        )
    ]

    message = notifier._format_earnings_watchlist_message(
        analyses=analyses,
        generated_at=datetime(2026, 4, 6, 8, 0, tzinfo=timezone.utc),
        limit=20,
    )

    assert "Earnings Watchlist" in message
    assert "AAA" in message
    assert "score 88.4" in message
