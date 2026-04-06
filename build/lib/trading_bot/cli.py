from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone

from alpaca.data.timeframe import TimeFrame

from trading_bot.adapters import AlpacaBroker
from trading_bot.backtest import run_backtest
from trading_bot.bot import TradingBot
from trading_bot.config import BotConfig, load_config
from trading_bot.domain import AssetClass, Instrument, StrategyKind
from trading_bot.event_feed import FileEventFeed
from trading_bot.persistence import JsonlTradeLogger
from trading_bot.risk import RiskManager
from trading_bot.strategy import (
    MovingAverageCrossStrategy,
    NewsShockStrategy,
    NewsSurpriseStrategy,
    SocialReactionStrategy,
    TradingStrategy,
)
from trading_bot.surprise_provider import build_structured_event_feed


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.log_level)
    try:
        config = load_config()

        if args.command == "run-once":
            run_once(config)
            return 0

        if args.command == "run-paper":
            run_paper(config)
            return 0

        if args.command == "backtest":
            run_backtest_command(config, args.symbol, args.asset_class, args.days, args.initial_cash)
            return 0

        parser.print_help()
        return 1
    except KeyboardInterrupt:
        logging.getLogger("trading_bot").warning("Avbrutt av bruker.")
        return 130
    except (RuntimeError, ValueError) as exc:
        logging.getLogger("trading_bot").error("%s", exc)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Paper trading bot for stocks and crypto.")
    parser.add_argument("--log-level", default="INFO")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("run-once", help="Kjør én vurderingsrunde.")
    subparsers.add_parser("run-paper", help="Kjør boten kontinuerlig i paper mode.")

    backtest_parser = subparsers.add_parser("backtest", help="Kjør enkel backtest.")
    backtest_parser.add_argument("--symbol", required=True)
    backtest_parser.add_argument(
        "--asset-class",
        required=True,
        choices=[asset_class.value for asset_class in AssetClass],
    )
    backtest_parser.add_argument("--days", type=int, default=120)
    backtest_parser.add_argument("--initial-cash", type=float, default=10_000)
    return parser


def configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def build_strategy(config: BotConfig) -> TradingStrategy:
    if config.strategy_kind is StrategyKind.TECHNICAL:
        return MovingAverageCrossStrategy(
            short_window=config.short_window,
            long_window=config.long_window,
        )
    if config.strategy_kind is StrategyKind.SOCIAL:
        return SocialReactionStrategy(
            watched_authors=config.social_watch_authors,
            min_sentiment_score=config.social_min_sentiment_score,
            min_engagement_score=config.social_min_engagement_score,
        )
    if config.strategy_kind is StrategyKind.NEWS:
        return NewsSurpriseStrategy(
            buy_surprise_threshold=config.news_buy_surprise_threshold,
            sell_surprise_threshold=config.news_sell_surprise_threshold,
            min_sentiment_score=config.news_min_sentiment_score,
        )
    if config.strategy_kind is StrategyKind.NEWS_SHOCK:
        return NewsShockStrategy(
            min_surprise=config.news_shock_min_surprise,
            min_confidence=config.news_shock_min_confidence,
            min_sentiment=config.news_shock_min_sentiment,
            confirmation_bars=config.news_shock_confirmation_bars,
            volume_multiplier=config.news_shock_volume_multiplier,
            max_event_age_seconds=config.news_shock_max_event_age_seconds,
            btc_max_hold_minutes=config.news_shock_btc_max_hold_minutes,
            stock_flatten_minutes_before_close=config.news_shock_stock_flatten_minutes_before_close,
            target_leverage=config.news_shock_target_leverage,
        )
    raise RuntimeError(f"Ukjent strategi: {config.strategy_kind}")


def build_risk_manager(config: BotConfig) -> RiskManager:
    return RiskManager(
        risk_per_trade=config.risk_per_trade,
        cash_buffer=config.cash_buffer,
        max_open_positions=config.max_open_positions,
        min_notional_usd=config.min_notional_usd,
    )


def build_event_feed(config: BotConfig) -> FileEventFeed | None:
    if config.social_feed_path is None and config.news_feed_path is None:
        return None
    return FileEventFeed(
        social_feed_path=config.social_feed_path,
        news_feed_path=config.news_feed_path,
    )


def build_broker(config: BotConfig) -> AlpacaBroker:
    return AlpacaBroker(
        api_key=config.alpaca_api_key,
        api_secret=config.alpaca_api_secret,
        paper=config.alpaca_paper,
        stock_feed=config.alpaca_stock_feed,
    )


def run_once(config: BotConfig) -> None:
    config.require_trading_credentials()
    config.require_news_shock_provider()
    bot = TradingBot(
        provider=build_broker(config),
        strategy=build_strategy(config),
        risk_manager=build_risk_manager(config),
        config=config,
        event_feed=build_event_feed(config),
        structured_event_feed=build_structured_event_feed(
            config.surprise_provider,
            config.finnhub_api_key,
            config.structured_events_path,
        ),
        trade_logger=JsonlTradeLogger(config.trade_log_path),
    )
    bot.run_once()


def run_paper(config: BotConfig) -> None:
    config.require_trading_credentials()
    config.require_news_shock_provider()
    bot = TradingBot(
        provider=build_broker(config),
        strategy=build_strategy(config),
        risk_manager=build_risk_manager(config),
        config=config,
        event_feed=build_event_feed(config),
        structured_event_feed=build_structured_event_feed(
            config.surprise_provider,
            config.finnhub_api_key,
            config.structured_events_path,
        ),
        trade_logger=JsonlTradeLogger(config.trade_log_path),
    )
    bot.run_forever()


def run_backtest_command(
    config: BotConfig,
    symbol: str,
    asset_class_raw: str,
    days: int,
    initial_cash: float,
) -> None:
    instrument = Instrument(symbol=symbol, asset_class=AssetClass(asset_class_raw))
    config.require_market_data_credentials(instrument.asset_class)
    broker = build_broker(config)
    strategy = build_strategy(config)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    bars = broker.get_historical_bars(
        instrument=instrument,
        start=start,
        end=end,
        timeframe=TimeFrame.Day if config.strategy_kind is not StrategyKind.NEWS_SHOCK else TimeFrame.Minute,
    )
    if not bars:
        raise RuntimeError(f"Ingen historiske bars mottatt for {symbol}.")

    structured_event_feed = build_structured_event_feed(
        config.surprise_provider,
        config.finnhub_api_key,
        config.structured_events_path,
    )
    events = ()
    if structured_event_feed is not None:
        events = structured_event_feed.get_recent_structured_events(
            instrument=instrument,
            since=start,
            until=end,
        )
    result = run_backtest(
        instrument=instrument,
        bars=bars,
        strategy=strategy,
        risk_manager=build_risk_manager(config),
        initial_cash=initial_cash,
        events=events,
    )
    print(f"Instrument: {result.instrument.symbol} ({result.instrument.asset_class.value})")
    print(f"Strategy: {config.strategy_kind.value}")
    print(f"Structured events: {len(events)}")
    print(f"Trades: {len(result.trades)}")
    print(f"Initial cash: {result.initial_cash:.2f}")
    print(f"Ending cash: {result.ending_cash:.2f}")
    print(f"Ending position qty: {result.ending_position_qty:.6f}")
    print(f"Ending equity: {result.ending_equity:.2f}")
    print(f"Return: {result.return_pct:.2f}%")


if __name__ == "__main__":
    raise SystemExit(main())
