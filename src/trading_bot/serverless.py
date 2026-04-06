from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from trading_bot.cli import (
    build_broker,
    build_earnings_bot_with_overrides,
    build_event_feed,
    build_risk_manager,
    build_strategy,
    build_telegram_notifier,
)
from trading_bot.config import BotConfig
from trading_bot.domain import Instrument, StrategyKind, StructuredEvent, symbol_in_scope
from trading_bot.persistence import JsonlTradeLogger
from trading_bot.state_store import build_state_store
from trading_bot.bot import TradingBot
from trading_bot.surprise_provider import (
    CompositeStructuredEventFeed,
    StructuredEventFeed,
    build_structured_event_feed,
)


class InMemoryStructuredEventFeed:
    def __init__(self, events: tuple[StructuredEvent, ...]) -> None:
        self.events = events

    def get_recent_structured_events(
        self,
        instrument: Instrument,
        since: datetime,
        until: datetime,
    ) -> tuple[StructuredEvent, ...]:
        return tuple(
            event
            for event in self.events
            if since <= event.published_at <= until
            and symbol_in_scope(instrument.symbol, event.instrument_scope)
        )


def run_serverless_news_shock(
    config: BotConfig,
    *,
    triggering_events: tuple[StructuredEvent, ...] = (),
) -> dict[str, object]:
    state_store = build_state_store(config)
    control_state = state_store.get_control_state()
    strategy_settings = state_store.get_strategy_settings()
    effective_config = replace(
        config,
        strategy_kind=StrategyKind.NEWS_SHOCK,
        dry_run=control_state.dry_run_override if control_state.dry_run_override is not None else config.dry_run,
    )
    effective_config.require_trading_credentials()
    if not triggering_events:
        effective_config.require_news_shock_provider()

    instruments = tuple(
        instrument
        for instrument in effective_config.instruments
        if not triggering_events
        or any(symbol_in_scope(instrument.symbol, event.instrument_scope) for event in triggering_events)
    )
    if not instruments:
        return {
            "ok": True,
            "ran": False,
            "reason": "No configured instruments matched the incoming events.",
            "trigger_event_count": len(triggering_events),
        }

    dynamic_feed = _build_dynamic_structured_event_feed(effective_config, triggering_events)
    runtime_state = state_store.load_runtime_state()
    bot = TradingBot(
        provider=build_broker(effective_config),
        strategy=build_strategy(effective_config, strategy_settings=strategy_settings),
        risk_manager=build_risk_manager(effective_config),
        config=replace(effective_config, instruments=instruments),
        event_feed=build_event_feed(effective_config),
        structured_event_feed=dynamic_feed,
        trade_logger=JsonlTradeLogger(effective_config.trade_log_path),
        runtime_state=runtime_state,
        telegram_notifier=build_telegram_notifier(effective_config),
        state_store=state_store,
        bot_control_state=control_state,
    )
    if triggering_events:
        state_store.record_news_events(triggering_events)
    bot.run_once()
    state_store.save_runtime_state(bot.runtime_state)
    state_store.record_heartbeat(
        status="ok",
        strategy="news_shock",
        details={
            "instrument_count": len(instruments),
            "trigger_event_count": len(triggering_events),
            "bot_enabled": control_state.bot_enabled,
            "dry_run": effective_config.dry_run,
        },
    )
    return {
        "ok": True,
        "ran": True,
        "strategy": "news_shock",
        "instrument_count": len(instruments),
        "instruments": [instrument.symbol for instrument in instruments],
        "trigger_event_count": len(triggering_events),
        "bot_enabled": control_state.bot_enabled,
        "dry_run": effective_config.dry_run,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def run_serverless_earnings_scan(config: BotConfig) -> dict[str, object]:
    state_store = build_state_store(config)
    control_state = state_store.get_control_state()
    strategy_settings = state_store.get_strategy_settings()
    effective_config = replace(
        config,
        strategy_kind=StrategyKind.EARNINGS_SURPRISE,
        dry_run=control_state.dry_run_override if control_state.dry_run_override is not None else config.dry_run,
    )
    effective_config.require_trading_credentials()
    effective_config.require_earnings_provider()
    bot = build_earnings_bot_with_overrides(
        effective_config,
        strategy_settings=strategy_settings,
        runtime_state=state_store.load_runtime_state(),
        state_store=state_store,
        bot_control_state=control_state,
    )
    analyses = bot.scan_once(send_watchlist=effective_config.earnings_telegram_watchlist_enabled)
    state_store.save_runtime_state(bot.runtime_state)
    state_store.record_heartbeat(
        status="ok",
        strategy="earnings_scan",
        details={
            "scan_candidates": len(analyses),
            "bot_enabled": control_state.bot_enabled,
            "dry_run": effective_config.dry_run,
        },
    )
    return {
        "ok": True,
        "ran": True,
        "strategy": "earnings_surprise",
        "scan_candidates": len(analyses),
        "top_symbols": [analysis.candidate.symbol for analysis in analyses[: effective_config.earnings_watchlist_limit]],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def run_serverless_earnings_once(config: BotConfig) -> dict[str, object]:
    state_store = build_state_store(config)
    control_state = state_store.get_control_state()
    strategy_settings = state_store.get_strategy_settings()
    effective_config = replace(
        config,
        strategy_kind=StrategyKind.EARNINGS_SURPRISE,
        dry_run=control_state.dry_run_override if control_state.dry_run_override is not None else config.dry_run,
    )
    effective_config.require_trading_credentials()
    effective_config.require_earnings_provider()
    bot = build_earnings_bot_with_overrides(
        effective_config,
        strategy_settings=strategy_settings,
        runtime_state=state_store.load_runtime_state(),
        state_store=state_store,
        bot_control_state=control_state,
    )
    bot.run_once()
    state_store.save_runtime_state(bot.runtime_state)
    state_store.record_heartbeat(
        status="ok",
        strategy="earnings_surprise",
        details={
            "tracked_symbols": sorted(bot.current_analyses.keys()),
            "tracked_count": len(bot.current_analyses),
            "bot_enabled": control_state.bot_enabled,
            "dry_run": effective_config.dry_run,
        },
    )
    return {
        "ok": True,
        "ran": True,
        "strategy": "earnings_surprise",
        "tracked_symbols": sorted(bot.current_analyses.keys()),
        "tracked_count": len(bot.current_analyses),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _build_dynamic_structured_event_feed(
    config: BotConfig,
    events: tuple[StructuredEvent, ...],
) -> StructuredEventFeed | None:
    feeds: list[StructuredEventFeed] = []
    if events:
        feeds.append(InMemoryStructuredEventFeed(events))

    static_feed = build_structured_event_feed(
        config.surprise_provider,
        config.finnhub_api_key,
        config.structured_events_path,
        vercel_webhook_logs_enabled=False,
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
    )
    if static_feed is not None:
        feeds.append(static_feed)
    if not feeds:
        return None
    if len(feeds) == 1:
        return feeds[0]
    return CompositeStructuredEventFeed(tuple(feeds))
