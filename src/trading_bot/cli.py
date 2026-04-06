from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone

from alpaca.data.timeframe import TimeFrame

from trading_bot.adapters import AlpacaBroker, SaxoBroker
from trading_bot.backtest import run_backtest
from trading_bot.bot import TradingBot
from trading_bot.config import BotConfig, load_config
from trading_bot.earnings_bot import EarningsTradingBot, run_earnings_backtest
from trading_bot.earnings_provider import EarningsUniverseScanner
from trading_bot.domain import AssetClass, BrokerKind, Instrument, StrategyKind
from trading_bot.event_feed import FileEventFeed
from trading_bot.notifications import TelegramNotifier
from trading_bot.persistence import EarningsDatabase, JsonlTradeLogger
from trading_bot.risk import RiskManager
from trading_bot.domain import StrategySetting
from trading_bot.strategy import (
    EarningsSurpriseStrategy,
    MovingAverageCrossStrategy,
    NewsShockStrategy,
    NewsSurpriseStrategy,
    SocialReactionStrategy,
    TradingStrategy,
)
from trading_bot.surprise_provider import build_structured_event_feed
from trading_bot.x_webhooks import XWebhookClient
from trading_bot.x_stream import XFilteredStreamWorker


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

        if args.command == "scan-earnings":
            scan_earnings(config)
            return 0

        if args.command == "run-earnings":
            run_earnings(config)
            return 0

        if args.command == "backtest":
            run_backtest_command(config, args.symbol, args.asset_class, args.days, args.initial_cash)
            return 0

        if args.command == "backtest-earnings":
            run_backtest_earnings_command(
                config,
                args.from_date,
                args.to_date,
                args.initial_cash,
            )
            return 0

        if args.command == "setup-x-webhook":
            setup_x_webhook(config)
            return 0

        if args.command == "run-x-stream":
            run_x_stream(config)
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
    subparsers.add_parser("scan-earnings", help="Bygg earnings-univers og send watchlist.")
    subparsers.add_parser("run-earnings", help="Kjør earnings-boten kontinuerlig.")
    subparsers.add_parser("setup-x-webhook", help="Registrer og link X filtered stream webhook.")
    subparsers.add_parser("run-x-stream", help="Kjør vedvarende X filtered stream-worker for realtime trades.")

    backtest_parser = subparsers.add_parser("backtest", help="Kjør enkel backtest.")
    backtest_parser.add_argument("--symbol", required=True)
    backtest_parser.add_argument(
        "--asset-class",
        required=True,
        choices=[asset_class.value for asset_class in AssetClass],
    )
    backtest_parser.add_argument("--days", type=int, default=120)
    backtest_parser.add_argument("--initial-cash", type=float, default=10_000)

    earnings_backtest_parser = subparsers.add_parser(
        "backtest-earnings",
        help="Kjør earnings-replay for dagens earnings-strategi.",
    )
    earnings_backtest_parser.add_argument("--from", dest="from_date", required=True)
    earnings_backtest_parser.add_argument("--to", dest="to_date", required=True)
    earnings_backtest_parser.add_argument("--initial-cash", type=float, default=10_000)
    return parser


def configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def build_strategy(
    config: BotConfig,
    *,
    strategy_settings: dict[str, StrategySetting] | None = None,
) -> TradingStrategy:
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
            min_source_count=config.news_shock_min_source_count,
            confirmation_bars=config.news_shock_confirmation_bars,
            volume_multiplier=config.news_shock_volume_multiplier,
            max_event_age_seconds=config.news_shock_max_event_age_seconds,
            realtime_window_seconds=config.news_shock_realtime_window_seconds,
            btc_max_hold_minutes=config.news_shock_btc_max_hold_minutes,
            stock_flatten_minutes_before_close=config.news_shock_stock_flatten_minutes_before_close,
            target_leverage=config.news_shock_target_leverage,
            btc_min_surprise=config.news_shock_btc_min_surprise,
            btc_min_confidence=config.news_shock_btc_min_confidence,
            btc_min_sentiment=config.news_shock_btc_min_sentiment,
            btc_min_source_count=config.news_shock_btc_min_source_count,
            btc_confirmation_bars=config.news_shock_btc_confirmation_bars,
            btc_volume_multiplier=config.news_shock_btc_volume_multiplier,
            btc_momentum_fade_bars=config.news_shock_btc_momentum_fade_bars,
            btc_momentum_fade_min_profit_pct=config.news_shock_btc_momentum_fade_min_profit_pct,
            btc_momentum_fade_from_high_pct=config.news_shock_btc_momentum_fade_from_high_pct,
            oil_proxy_symbols=config.oil_proxy_symbols,
            oil_min_trade_score=config.news_shock_oil_min_trade_score,
            oil_min_confidence=config.news_shock_oil_min_confidence,
            oil_confirmation_bars=config.news_shock_oil_confirmation_bars,
            oil_volume_multiplier=config.news_shock_oil_volume_multiplier,
            oil_risk_per_trade=config.news_shock_oil_risk_per_trade,
            strategy_settings=strategy_settings,
        )
    if config.strategy_kind is StrategyKind.EARNINGS_SURPRISE:
        return build_earnings_strategy(config, strategy_settings=strategy_settings)
    raise RuntimeError(f"Ukjent strategi: {config.strategy_kind}")


def build_earnings_strategy(
    config: BotConfig,
    *,
    strategy_settings: dict[str, StrategySetting] | None = None,
) -> TradingStrategy:
    return EarningsSurpriseStrategy(
        min_eps_surprise_pct=config.earnings_min_eps_surprise_pct,
        min_revenue_surprise_pct=config.earnings_min_revenue_surprise_pct,
        max_event_age_seconds=config.earnings_max_event_age_seconds,
        confirmation_bars=config.earnings_confirmation_bars,
        volume_multiplier=config.earnings_volume_multiplier,
        min_risk_multiplier=config.earnings_risk_multiplier_min,
        max_risk_multiplier=config.earnings_risk_multiplier_max,
        flatten_minutes_before_close=10,
        strategy_settings=strategy_settings,
    )


def setup_x_webhook(config: BotConfig) -> None:
    if not config.x_bearer_token:
        raise RuntimeError("X_BEARER_TOKEN må være satt.")
    if not config.x_consumer_secret:
        raise RuntimeError("X_CONSUMER_SECRET må være satt for CRC/signatur på webhook-endepunktet.")
    if not config.x_webhook_url:
        raise RuntimeError("X_WEBHOOK_URL må være satt til den offentlige /api/x-webhook-URL-en.")

    client = XWebhookClient(config.x_bearer_token)
    result = client.ensure_filtered_stream_webhook(
        webhook_url=config.x_webhook_url,
        rule_value=config.x_filtered_stream_rule,
        rule_tag=config.x_filtered_stream_rule_tag,
    )
    print("X realtime webhook er klar.")
    print(f"webhook_id={result.webhook_id}")
    print(f"url={result.webhook_url}")
    print(f"created={result.created}")
    print(f"validated={result.validated}")
    print(f"linked={result.linked}")
    print(f"rule_tag={result.rule_tag}")


def run_x_stream(config: BotConfig) -> None:
    config.require_trading_credentials()
    if not config.x_bearer_token:
        raise RuntimeError("X_BEARER_TOKEN må være satt.")
    worker = XFilteredStreamWorker(config)
    worker.run_forever()


def build_risk_manager(config: BotConfig) -> RiskManager:
    return RiskManager(
        risk_per_trade=config.risk_per_trade,
        cash_buffer=config.cash_buffer,
        max_open_positions=config.max_open_positions,
        min_notional_usd=config.min_notional_usd,
    )


def build_earnings_risk_manager(config: BotConfig) -> RiskManager:
    return RiskManager(
        risk_per_trade=config.earnings_risk_per_trade,
        cash_buffer=config.cash_buffer,
        max_open_positions=config.earnings_max_open_positions,
        min_notional_usd=config.min_notional_usd,
    )


def build_event_feed(config: BotConfig) -> FileEventFeed | None:
    if config.social_feed_path is None and config.news_feed_path is None:
        return None
    return FileEventFeed(
        social_feed_path=config.social_feed_path,
        news_feed_path=config.news_feed_path,
    )


def build_telegram_notifier(config: BotConfig) -> TelegramNotifier | None:
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return None
    return TelegramNotifier(
        bot_token=config.telegram_bot_token,
        chat_id=config.telegram_chat_id,
        disable_notification=config.telegram_disable_notification,
        message_thread_id=config.telegram_message_thread_id,
    )


def build_broker(config: BotConfig):
    if config.broker_kind is BrokerKind.SAXO:
        return SaxoBroker(
            access_token=config.saxo_access_token,
            environment=config.saxo_environment,
            account_key=config.saxo_account_key,
            default_exchange_id=config.saxo_default_exchange_id,
            client_key=config.saxo_client_key,
            instrument_map=config.saxo_instrument_map,
        )
    return AlpacaBroker(
        api_key=config.alpaca_api_key,
        api_secret=config.alpaca_api_secret,
        paper=config.alpaca_paper,
        stock_feed=config.alpaca_stock_feed,
    )


def build_earnings_scanner(config: BotConfig, broker) -> EarningsUniverseScanner:
    return EarningsUniverseScanner(
        broker=broker,
        finnhub_api_key=config.finnhub_api_key or "",
        sec_user_agent=config.sec_api_user_agent,
        database=EarningsDatabase(config.earnings_db_path),
    )


def build_earnings_bot(config: BotConfig) -> EarningsTradingBot:
    return build_earnings_bot_with_overrides(config)


def build_earnings_bot_with_overrides(
    config: BotConfig,
    *,
    strategy_settings: dict[str, StrategySetting] | None = None,
    runtime_state=None,
    state_store=None,
    bot_control_state=None,
) -> EarningsTradingBot:
    broker = build_broker(config)
    database = EarningsDatabase(config.earnings_db_path)
    return EarningsTradingBot(
        provider=broker,
        scanner=EarningsUniverseScanner(
            broker=broker,
            finnhub_api_key=config.finnhub_api_key or "",
            sec_user_agent=config.sec_api_user_agent,
            database=database,
        ),
        strategy=build_earnings_strategy(config, strategy_settings=strategy_settings),
        risk_manager=build_earnings_risk_manager(config),
        config=config,
        database=database,
        trade_logger=JsonlTradeLogger(config.trade_log_path),
        runtime_state=runtime_state,
        telegram_notifier=build_telegram_notifier(config),
        state_store=state_store,
        bot_control_state=bot_control_state,
    )


def require_earnings_setup(config: BotConfig) -> None:
    if config.broker_kind is not BrokerKind.ALPACA:
        raise RuntimeError("earnings_surprise støtter foreløpig bare Alpaca.")
    if not config.finnhub_api_key:
        raise RuntimeError("earnings_surprise krever FINNHUB_API_KEY.")


def run_once(config: BotConfig) -> None:
    if config.strategy_kind is StrategyKind.EARNINGS_SURPRISE:
        config.require_trading_credentials()
        config.require_earnings_provider()
        build_earnings_bot(config).run_once()
        return
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
            vercel_webhook_logs_enabled=config.vercel_webhook_logs_enabled,
            vercel_webhook_scope=config.vercel_webhook_scope,
            vercel_webhook_environment=config.vercel_webhook_environment,
            vercel_webhook_logs_since_minutes=config.vercel_webhook_logs_since_minutes,
            vercel_webhook_cwd=config.vercel_webhook_cwd,
            official_rss_feeds_enabled=config.official_rss_feeds_enabled,
            official_rss_feeds=config.official_rss_feeds,
            sec_tsla_submissions_enabled=config.sec_tsla_submissions_enabled,
            sec_api_user_agent=config.sec_api_user_agent,
            x_recent_search_enabled=config.x_recent_search_enabled,
            x_bearer_token=config.x_bearer_token,
            x_recent_search_query=config.x_recent_search_query,
            x_recent_search_max_results=config.x_recent_search_max_results,
        ),
        trade_logger=JsonlTradeLogger(config.trade_log_path),
        telegram_notifier=build_telegram_notifier(config),
    )
    bot.run_once()


def run_paper(config: BotConfig) -> None:
    if config.strategy_kind is StrategyKind.EARNINGS_SURPRISE:
        config.require_trading_credentials()
        config.require_earnings_provider()
        build_earnings_bot(config).run_forever()
        return
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
            vercel_webhook_logs_enabled=config.vercel_webhook_logs_enabled,
            vercel_webhook_scope=config.vercel_webhook_scope,
            vercel_webhook_environment=config.vercel_webhook_environment,
            vercel_webhook_logs_since_minutes=config.vercel_webhook_logs_since_minutes,
            vercel_webhook_cwd=config.vercel_webhook_cwd,
            official_rss_feeds_enabled=config.official_rss_feeds_enabled,
            official_rss_feeds=config.official_rss_feeds,
            sec_tsla_submissions_enabled=config.sec_tsla_submissions_enabled,
            sec_api_user_agent=config.sec_api_user_agent,
            x_recent_search_enabled=config.x_recent_search_enabled,
            x_bearer_token=config.x_bearer_token,
            x_recent_search_query=config.x_recent_search_query,
            x_recent_search_max_results=config.x_recent_search_max_results,
        ),
        trade_logger=JsonlTradeLogger(config.trade_log_path),
        telegram_notifier=build_telegram_notifier(config),
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
        vercel_webhook_logs_enabled=config.vercel_webhook_logs_enabled,
        vercel_webhook_scope=config.vercel_webhook_scope,
        vercel_webhook_environment=config.vercel_webhook_environment,
        vercel_webhook_logs_since_minutes=config.vercel_webhook_logs_since_minutes,
        vercel_webhook_cwd=config.vercel_webhook_cwd,
        official_rss_feeds_enabled=config.official_rss_feeds_enabled,
        official_rss_feeds=config.official_rss_feeds,
        sec_tsla_submissions_enabled=config.sec_tsla_submissions_enabled,
        sec_api_user_agent=config.sec_api_user_agent,
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


def scan_earnings(config: BotConfig) -> None:
    config.require_trading_credentials()
    require_earnings_setup(config)
    bot = build_earnings_bot(config)
    analyses = bot.scan_once(send_watchlist=config.earnings_telegram_watchlist_enabled)
    print(f"Earnings candidates: {len(analyses)}")
    for index, analysis in enumerate(analyses[: config.earnings_watchlist_limit], start=1):
        candidate = analysis.candidate
        print(
            f"{index:>2}. {candidate.symbol} score={analysis.score:.1f} "
            f"date={candidate.earnings_date.isoformat()} hour={candidate.earnings_hour or 'tbd'} "
            f"price={candidate.last_price:.2f} avg_dollar_vol={candidate.avg_dollar_volume_usd:.0f}"
        )
        if analysis.reasons:
            print(f"    {analysis.reasons[0]}")


def run_earnings(config: BotConfig) -> None:
    config.require_trading_credentials()
    require_earnings_setup(config)
    build_earnings_bot(config).run_forever()


def run_backtest_earnings_command(
    config: BotConfig,
    from_date_raw: str,
    to_date_raw: str,
    initial_cash: float,
) -> None:
    config.require_trading_credentials()
    require_earnings_setup(config)
    broker = build_broker(config)
    scanner = build_earnings_scanner(config, broker)
    from_datetime = datetime.fromisoformat(from_date_raw).replace(tzinfo=timezone.utc)
    to_datetime = datetime.fromisoformat(to_date_raw).replace(tzinfo=timezone.utc)
    summary = run_earnings_backtest(
        broker=broker,
        scanner=scanner,
        strategy=build_earnings_strategy(config),
        risk_manager=build_earnings_risk_manager(config),
        config=config,
        from_datetime=from_datetime,
        to_datetime=to_datetime,
        initial_cash=initial_cash,
    )
    print(f"Earnings scan candidates: {len(summary['analyses'])}")
    print(f"Symbols backtested: {len(summary['results'])}")
    print(f"Trades: {summary['total_trades']}")
    print(f"Ending equity: {summary['ending_equity']:.2f}")
    for result in summary["results"][:10]:
        print(
            f"{result.instrument.symbol}: trades={len(result.trades)} "
            f"ending_equity={result.ending_equity:.2f} return={result.return_pct:.2f}%"
        )


if __name__ == "__main__":
    raise SystemExit(main())
