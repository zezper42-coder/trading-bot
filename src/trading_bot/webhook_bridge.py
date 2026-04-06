from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from trading_bot.domain import (
    StructuredEvent,
    StructuredEventCategory,
    canonical_symbol,
)
from trading_bot.event_feed import parse_structured_event

VERCEL_STRUCTURED_EVENT_LOG = "finnhub_structured_event"

POSITIVE_NEWS_KEYWORDS: dict[str, float] = {
    "approval": 1.2,
    "approved": 1.2,
    "beat": 1.1,
    "beats": 1.1,
    "above expectations": 1.1,
    "surprise": 1.0,
    "unexpected": 1.0,
    "record": 0.9,
    "partners": 0.8,
    "partnership": 0.8,
    "adoption": 0.8,
    "adopts": 0.8,
    "etf": 0.8,
    "inflow": 0.8,
    "inflows": 0.8,
    "strong demand": 0.7,
    "raises guidance": 1.0,
    "expands": 0.6,
    "wins": 0.8,
    "secures": 0.8,
    "sooner than expected": 0.9,
    "earlier than expected": 0.9,
    "ahead of schedule": 0.9,
    "softens": 0.7,
    "cools": 0.7,
    "rate cut": 1.0,
    "institutional": 0.6,
    "treasury": 0.9,
    "reserve": 0.9,
    "buys bitcoin": 1.1,
    "buying bitcoin": 1.1,
    "adds bitcoin": 1.0,
    "accumulates bitcoin": 1.1,
    "bitcoin reserve": 1.1,
}

NEGATIVE_NEWS_KEYWORDS: dict[str, float] = {
    "miss": 1.2,
    "misses": 1.2,
    "delay": 0.8,
    "delays": 0.8,
    "lawsuit": 0.8,
    "hack": 1.0,
    "probe": 0.8,
    "falls": 0.6,
    "drop": 0.6,
    "plunge": 1.0,
    "recall": 1.0,
    "rejected": 1.2,
    "denied": 1.2,
    "sells bitcoin": 1.1,
    "bitcoin outflows": 0.9,
    "liquidates bitcoin": 1.0,
    "ban": 1.0,
    "bans": 1.0,
    "exploit": 1.0,
}

BTC_KEYWORDS = ("bitcoin", "btc", "btc/usd")
TSLA_KEYWORDS = ("tsla", "tesla")
MACRO_KEYWORDS = ("cpi", "ppi", "nonfarm", "nfp", "fomc", "interest rate", "fed")
TRUMP_KEYWORDS = ("trump", "donald trump")
WHITE_HOUSE_KEYWORDS = ("white house", "presidential", "executive order", "administration")
OIL_KEYWORDS = (
    "oil",
    "crude",
    "brent",
    "wti",
    "petroleum",
    "energy",
    "pipeline",
    "refinery",
    "drilling",
    "opec",
)
BULLISH_OIL_KEYWORDS: dict[str, float] = {
    "sanction": 1.0,
    "sanctions": 1.0,
    "tariff": 0.8,
    "tariffs": 0.8,
    "supply cut": 1.0,
    "production cut": 1.0,
    "export halt": 1.0,
    "export ban": 1.0,
    "refinery outage": 0.9,
    "middle east tension": 0.9,
    "shipping disruption": 0.9,
}
BEARISH_OIL_KEYWORDS: dict[str, float] = {
    "increase output": 1.0,
    "production increase": 1.0,
    "supply increase": 1.0,
    "drilling expansion": 0.9,
    "ceasefire": 0.8,
    "output boost": 0.9,
    "release reserves": 1.0,
    "strategic reserve release": 1.0,
}
OIL_PROXY_SYMBOLS = ("USO", "XLE", "OXY", "XOM", "CVX", "SLB")
HIGH_IMPACT_X_USERNAMES = {
    "elonmusk",
    "realdonaldtrump",
    "whitehouse",
    "secgov",
    "federalreserve",
    "ustreasury",
    "saylor",
    "bitcoinmagazine",
    "tier10k",
    "kobeissiletter",
    "watcherguru",
    "deitaone",
    "unusual_whales",
    "zerohedge",
    "financialjuice",
    "dbnewsdesk",
}


@dataclass(frozen=True)
class HeuristicScore:
    surprise_score: float
    sentiment_score: float
    confidence_score: float


def normalize_finnhub_webhook(
    payload: Any,
    received_at: datetime | None = None,
) -> tuple[StructuredEvent, ...]:
    if received_at is None:
        received_at = datetime.now(timezone.utc)
    envelope_event = ""
    if isinstance(payload, dict):
        envelope_event = str(payload.get("event", "")).strip().lower()
    return normalize_news_payload(
        payload,
        received_at=received_at,
        envelope_event=envelope_event,
        event_id_namespace="finnhub-webhook",
        default_source="finnhub_webhook",
    )


def normalize_x_webhook(
    payload: Any,
    received_at: datetime | None = None,
) -> tuple[StructuredEvent, ...]:
    if received_at is None:
        received_at = datetime.now(timezone.utc)
    items = normalize_x_payload_to_items(payload, source_prefix="x_webhook")
    return normalize_news_items(
        items,
        received_at=received_at,
        envelope_event="x_webhook",
        event_id_namespace="x-webhook",
        default_source="x_webhook",
    )


def normalize_x_stream(
    payload: Any,
    received_at: datetime | None = None,
) -> tuple[StructuredEvent, ...]:
    if received_at is None:
        received_at = datetime.now(timezone.utc)
    items = normalize_x_payload_to_items(payload, source_prefix="x_stream")
    return normalize_news_items(
        items,
        received_at=received_at,
        envelope_event="x_stream",
        event_id_namespace="x-stream",
        default_source="x_stream",
    )


def normalize_news_payload(
    payload: Any,
    *,
    received_at: datetime | None = None,
    envelope_event: str = "",
    event_id_namespace: str = "external-event",
    default_source: str = "external_source",
) -> tuple[StructuredEvent, ...]:
    if received_at is None:
        received_at = datetime.now(timezone.utc)
    items = _extract_items(payload)
    return normalize_news_items(
        items,
        received_at=received_at,
        envelope_event=envelope_event,
        event_id_namespace=event_id_namespace,
        default_source=default_source,
    )


def normalize_news_items(
    items: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    *,
    received_at: datetime | None = None,
    envelope_event: str = "",
    event_id_namespace: str = "external-event",
    default_source: str = "external_source",
) -> tuple[StructuredEvent, ...]:
    if received_at is None:
        received_at = datetime.now(timezone.utc)
    events: list[StructuredEvent] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        event = _normalize_item(
            item,
            envelope_event,
            received_at,
            event_id_namespace=event_id_namespace,
            default_source=default_source,
        )
        if event is not None:
            events.append(event)
    events.sort(key=lambda event: event.published_at, reverse=True)
    return tuple(events)


def structured_event_to_record(event: StructuredEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "source": event.source,
        "instrument_scope": list(event.instrument_scope),
        "category": event.category.value,
        "published_at": event.published_at.isoformat(),
        "headline": event.headline,
        "actual_value": event.actual_value,
        "expected_value": event.expected_value,
        "surprise_score": event.surprise_score,
        "sentiment_score": event.sentiment_score,
        "confidence_score": event.confidence_score,
        "is_scheduled": event.is_scheduled,
        "headline_context": [
            {
                "headline": item.headline,
                "source": item.source,
                "created_at": item.created_at.isoformat(),
            }
            for item in event.headline_context
        ],
        "supporting_sources": list(event.supporting_sources),
        "source_count": event.source_count,
        "corroboration_score": event.corroboration_score,
        "theme": event.theme,
        "topic_tags": list(event.topic_tags),
        "entity_tags": list(event.entity_tags),
        "direction_score": event.direction_score,
        "magnitude_score": event.magnitude_score,
        "unexpectedness_score": event.unexpectedness_score,
        "trade_score": event.trade_score,
    }


def parse_vercel_log_output(raw_output: str) -> tuple[StructuredEvent, ...]:
    deduped: dict[str, StructuredEvent] = {}
    for raw_line in raw_output.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            log_record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = log_record.get("message")
        if not isinstance(message, str):
            continue
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue
        if payload.get("event") != VERCEL_STRUCTURED_EVENT_LOG:
            continue
        record = payload.get("record")
        if not isinstance(record, dict):
            continue
        event = parse_structured_event(record)
        deduped.setdefault(event.event_id, event)
    events = list(deduped.values())
    events.sort(key=lambda event: event.published_at, reverse=True)
    return tuple(events)


def normalize_x_payload_to_items(
    payload: Any,
    *,
    source_prefix: str,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, dict):
        return ()
    users_by_id: dict[str, dict[str, Any]] = {}
    includes = payload.get("includes")
    if isinstance(includes, dict):
        users = includes.get("users")
        if isinstance(users, list):
            for user in users:
                if isinstance(user, dict) and user.get("id") is not None:
                    users_by_id[str(user["id"])] = user

    items: list[dict[str, Any]] = []
    data = payload.get("data")
    post_items = [data] if isinstance(data, dict) else data if isinstance(data, list) else []
    if not isinstance(post_items, list):
        return ()
    for post in post_items:
        if not isinstance(post, dict):
            continue
        post_id = post.get("id")
        text = str(post.get("text") or "").strip()
        created_at = post.get("created_at")
        author = users_by_id.get(str(post.get("author_id") or ""), {})
        username = str(author.get("username") or "").strip()
        public_metrics = post.get("public_metrics") if isinstance(post.get("public_metrics"), dict) else {}
        summary_parts: list[str] = []
        if username:
            summary_parts.append(f"Post by @{username}")
        metrics_text = _format_x_metrics(public_metrics)
        if metrics_text:
            summary_parts.append(metrics_text)
        matching_rules = payload.get("matching_rules")
        if isinstance(matching_rules, list):
            tags = [str(rule.get("tag") or "").strip() for rule in matching_rules if isinstance(rule, dict)]
            tags = [tag for tag in tags if tag]
            if tags:
                summary_parts.append("rules=" + ",".join(tags[:4]))
        source = f"{source_prefix}:@{username}" if username else source_prefix
        items.append(
            {
                "id": post_id,
                "headline": text,
                "summary": " · ".join(summary_parts),
                "datetime": created_at,
                "related": _infer_x_related_hint(text),
                "source": source,
                "confidence_score": _x_confidence_score(
                    public_metrics,
                    bool(author.get("verified")),
                    username=username,
                ),
            }
        )
    return tuple(items)


def _extract_items(payload: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(payload, list):
        return tuple(item for item in payload if isinstance(item, dict))
    if not isinstance(payload, dict):
        return ()
    if isinstance(payload.get("data"), list):
        return tuple(item for item in payload["data"] if isinstance(item, dict))
    return (payload,)


def _normalize_item(
    item: dict[str, Any],
    envelope_event: str,
    received_at: datetime,
    *,
    event_id_namespace: str,
    default_source: str,
) -> StructuredEvent | None:
    headline = str(item.get("headline") or item.get("title") or item.get("event") or "").strip()
    summary = str(item.get("summary") or item.get("description") or "").strip()
    instrument_scope = _infer_instrument_scope(item, headline, summary)
    if not instrument_scope:
        return None

    published_at = _coerce_datetime(
        item.get("datetime")
        or item.get("time")
        or item.get("published_at")
        or item.get("date")
    ) or received_at

    actual_value = _coerce_float(
        item.get("actual") if item.get("actual") not in {None, ""} else item.get("actual_value")
    )
    expected_value = _coerce_float(
        item.get("estimate")
        if item.get("estimate") not in {None, ""}
        else item.get("expected")
        if item.get("expected") not in {None, ""}
        else item.get("expected_value")
        if item.get("expected_value") not in {None, ""}
        else item.get("consensus")
        if item.get("consensus") not in {None, ""}
        else item.get("forecast")
    )
    surprise_score = _coerce_surprise(item, actual_value, expected_value)
    sentiment_score = _coerce_float(item.get("sentiment_score") or item.get("sentiment"))
    confidence_score = _coerce_float(item.get("confidence_score") or item.get("confidence"))
    heuristic_score = _score_news_item(headline, summary, item)
    if surprise_score is None and heuristic_score is not None:
        surprise_score = heuristic_score.surprise_score
    if sentiment_score is None and heuristic_score is not None:
        sentiment_score = heuristic_score.sentiment_score
    if confidence_score is None and heuristic_score is not None:
        confidence_score = heuristic_score.confidence_score
    if surprise_score is None or sentiment_score is None or confidence_score is None:
        return None

    category = _infer_category(envelope_event, headline, instrument_scope, actual_value, expected_value)
    event_id = _build_event_id(
        item,
        instrument_scope,
        headline,
        published_at,
        namespace=event_id_namespace,
    )
    source = str(item.get("source") or default_source)

    return StructuredEvent(
        event_id=event_id,
        source=source,
        instrument_scope=instrument_scope,
        category=category,
        published_at=published_at,
        headline=headline or f"{source} webhook event",
        actual_value=actual_value,
        expected_value=expected_value,
        surprise_score=surprise_score,
        sentiment_score=sentiment_score,
        confidence_score=confidence_score,
        is_scheduled=bool(actual_value is not None and expected_value is not None)
        or envelope_event in {"earnings", "economic", "macro"},
        supporting_sources=(source,),
        source_count=1,
        corroboration_score=1.0,
        theme=_infer_theme(instrument_scope, headline, summary),
        topic_tags=_extract_topic_tags(headline, summary),
        entity_tags=_extract_entity_tags(headline, summary),
        direction_score=_infer_direction_score(
            instrument_scope=instrument_scope,
            headline=headline,
            summary=summary,
            sentiment_score=sentiment_score,
        ),
        magnitude_score=_infer_magnitude_score(
            headline=headline,
            summary=summary,
            surprise_score=surprise_score,
        ),
        unexpectedness_score=_infer_unexpectedness_score(
            headline=headline,
            summary=summary,
            surprise_score=surprise_score,
        ),
        trade_score=_infer_trade_score(
            instrument_scope=instrument_scope,
            headline=headline,
            summary=summary,
            surprise_score=surprise_score,
            sentiment_score=sentiment_score,
            confidence_score=confidence_score,
        ),
    )


def _infer_instrument_scope(
    item: dict[str, Any],
    headline: str,
    summary: str,
) -> tuple[str, ...]:
    scope: list[str] = []
    candidates: list[str] = []
    for raw_value in (item.get("symbol"), item.get("ticker"), item.get("related"), item.get("symbols")):
        if raw_value is None:
            continue
        if isinstance(raw_value, list):
            candidates.extend(str(value) for value in raw_value)
        else:
            candidates.extend(part.strip() for part in str(raw_value).replace("|", ",").split(","))

    text = f"{headline} {summary}".lower()
    normalized_candidates = {canonical_symbol(candidate) for candidate in candidates if candidate}
    if "TSLA" in normalized_candidates or any(keyword in text for keyword in TSLA_KEYWORDS):
        scope.append("TSLA")
    if "BTC" in normalized_candidates or "BTCUSD" in normalized_candidates or any(
        keyword in text for keyword in BTC_KEYWORDS
    ):
        scope.append("BTC/USD")
    elif any(keyword in text for keyword in MACRO_KEYWORDS):
        scope.append("BTC/USD")
    if (
        any(keyword in text for keyword in OIL_KEYWORDS)
        and (
            any(keyword in text for keyword in TRUMP_KEYWORDS)
            or any(keyword in text for keyword in WHITE_HOUSE_KEYWORDS)
            or "whitehouse" in text
        )
    ):
        scope.extend(symbol for symbol in OIL_PROXY_SYMBOLS if symbol not in scope)
    return tuple(scope)


def _infer_category(
    envelope_event: str,
    headline: str,
    instrument_scope: tuple[str, ...],
    actual_value: float | None,
    expected_value: float | None,
) -> StructuredEventCategory:
    headline_lower = headline.lower()
    if actual_value is not None and expected_value is not None and "TSLA" in instrument_scope:
        return StructuredEventCategory.EARNINGS
    if any(symbol in instrument_scope for symbol in OIL_PROXY_SYMBOLS):
        return StructuredEventCategory.ENERGY_POLICY
    if any(keyword in headline_lower for keyword in TRUMP_KEYWORDS):
        return StructuredEventCategory.GEOPOLITICAL
    if "BTC/USD" in instrument_scope and any(keyword in headline_lower for keyword in MACRO_KEYWORDS):
        return StructuredEventCategory.MACRO
    if envelope_event in {"economic", "macro"}:
        return StructuredEventCategory.MACRO
    if "earning" in headline_lower or "guidance" in headline_lower:
        return StructuredEventCategory.EARNINGS
    return StructuredEventCategory.OTHER


def _build_event_id(
    item: dict[str, Any],
    instrument_scope: tuple[str, ...],
    headline: str,
    published_at: datetime,
    *,
    namespace: str,
) -> str:
    if item.get("id") not in {None, ""}:
        return f"{namespace}-{item['id']}"
    seed = json.dumps(
        {
            "headline": headline,
            "published_at": published_at.isoformat(),
            "instrument_scope": list(instrument_scope),
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"{namespace}-{digest}"


def _format_x_metrics(public_metrics: object) -> str:
    if not isinstance(public_metrics, dict):
        return ""
    like_count = int(public_metrics.get("like_count") or 0)
    repost_count = int(public_metrics.get("retweet_count") or 0)
    reply_count = int(public_metrics.get("reply_count") or 0)
    quote_count = int(public_metrics.get("quote_count") or 0)
    parts: list[str] = []
    if like_count:
        parts.append(f"{like_count} likes")
    if repost_count:
        parts.append(f"{repost_count} reposts")
    if reply_count:
        parts.append(f"{reply_count} replies")
    if quote_count:
        parts.append(f"{quote_count} quotes")
    return ", ".join(parts)


def _x_confidence_score(public_metrics: object, verified: bool, *, username: str = "") -> float:
    base = 0.78 if verified else 0.74
    if not isinstance(public_metrics, dict):
        return min(0.95, base + _x_username_bonus(username))
    engagement = (
        int(public_metrics.get("like_count") or 0)
        + (2 * int(public_metrics.get("retweet_count") or 0))
        + int(public_metrics.get("reply_count") or 0)
        + int(public_metrics.get("quote_count") or 0)
    )
    engagement_bonus = min(0.14, engagement / 5000)
    return min(0.95, base + engagement_bonus + _x_username_bonus(username))


def _x_username_bonus(username: str) -> float:
    if username.strip().lower() in HIGH_IMPACT_X_USERNAMES:
        return 0.08
    return 0.0


def _infer_x_related_hint(text: str) -> str | None:
    normalized = text.lower()
    if "tesla" in normalized or "tsla" in normalized:
        return "TSLA"
    if "bitcoin" in normalized or " btc" in normalized or "$btc" in normalized:
        return "BTC"
    if any(keyword in normalized for keyword in ("oil", "crude", "energy", "opec", "drilling")):
        return "USO"
    return None


def _score_news_item(
    headline: str,
    summary: str,
    item: dict[str, Any],
) -> HeuristicScore | None:
    text = f"{headline} {summary}".lower()
    positive_score = sum(weight for keyword, weight in POSITIVE_NEWS_KEYWORDS.items() if keyword in text)
    negative_score = sum(weight for keyword, weight in NEGATIVE_NEWS_KEYWORDS.items() if keyword in text)
    net_score = positive_score - negative_score
    if net_score == 0 and any(keyword in text for keyword in OIL_KEYWORDS):
        bullish = sum(weight for keyword, weight in BULLISH_OIL_KEYWORDS.items() if keyword in text)
        bearish = sum(weight for keyword, weight in BEARISH_OIL_KEYWORDS.items() if keyword in text)
        net_score = bullish - bearish
        positive_score = bullish
        negative_score = bearish
    if net_score == 0:
        return None

    source_bonus = 0.03 if item.get("source") not in {None, "", "unknown"} else 0.0
    strength = max(positive_score, negative_score)
    surprise_strength = min(1.25, 0.42 + (0.18 * strength))
    sentiment_strength = min(0.95, 0.18 + (0.12 * strength))
    surprise_score = surprise_strength if net_score > 0 else -surprise_strength
    sentiment_score = sentiment_strength if net_score > 0 else -sentiment_strength
    confidence_score = min(0.95, 0.72 + (0.05 * strength) + source_bonus)
    return HeuristicScore(
        surprise_score=surprise_score,
        sentiment_score=sentiment_score,
        confidence_score=confidence_score,
    )


def _infer_theme(
    instrument_scope: tuple[str, ...],
    headline: str,
    summary: str,
) -> str:
    text = f"{headline} {summary}".lower()
    if any(symbol in instrument_scope for symbol in OIL_PROXY_SYMBOLS):
        return "oil_policy"
    if "BTC/USD" in instrument_scope:
        return "btc_news"
    if "TSLA" in instrument_scope:
        return "tsla_news"
    if any(keyword in text for keyword in TRUMP_KEYWORDS):
        return "trump_geopolitics"
    return "general_news"


def _extract_topic_tags(headline: str, summary: str) -> tuple[str, ...]:
    text = f"{headline} {summary}".lower()
    tags: list[str] = []
    if any(keyword in text for keyword in BTC_KEYWORDS):
        tags.append("bitcoin")
    if any(keyword in text for keyword in MACRO_KEYWORDS):
        tags.append("macro")
    if any(keyword in text for keyword in OIL_KEYWORDS):
        tags.append("oil")
    if "tariff" in text or "tariffs" in text:
        tags.append("tariffs")
    if "sanction" in text or "sanctions" in text:
        tags.append("sanctions")
    if "executive order" in text:
        tags.append("executive_order")
    return tuple(tags)


def _extract_entity_tags(headline: str, summary: str) -> tuple[str, ...]:
    text = f"{headline} {summary}".lower()
    tags: list[str] = []
    if any(keyword in text for keyword in TRUMP_KEYWORDS):
        tags.append("trump")
    if any(keyword in text for keyword in WHITE_HOUSE_KEYWORDS):
        tags.append("white_house")
    if "opec" in text:
        tags.append("opec")
    if "fed" in text:
        tags.append("fed")
    return tuple(tags)


def _infer_direction_score(
    *,
    instrument_scope: tuple[str, ...],
    headline: str,
    summary: str,
    sentiment_score: float,
) -> float:
    text = f"{headline} {summary}".lower()
    if any(symbol in instrument_scope for symbol in OIL_PROXY_SYMBOLS):
        bullish = sum(weight for keyword, weight in BULLISH_OIL_KEYWORDS.items() if keyword in text)
        bearish = sum(weight for keyword, weight in BEARISH_OIL_KEYWORDS.items() if keyword in text)
        net = bullish - bearish
        if net == 0:
            return 0.0
        return max(-1.0, min(1.0, net / 2.0))
    return max(-1.0, min(1.0, sentiment_score))


def _infer_magnitude_score(
    *,
    headline: str,
    summary: str,
    surprise_score: float,
) -> float:
    text = f"{headline} {summary}".lower()
    keyword_strength = 0.0
    keyword_strength += 0.2 if "unexpected" in text else 0.0
    keyword_strength += 0.2 if "surprise" in text else 0.0
    keyword_strength += 0.2 if "record" in text else 0.0
    keyword_strength += 0.2 if "executive order" in text else 0.0
    keyword_strength += 0.2 if "sanctions" in text or "tariffs" in text else 0.0
    return min(1.0, abs(surprise_score) + keyword_strength)


def _infer_unexpectedness_score(
    *,
    headline: str,
    summary: str,
    surprise_score: float,
) -> float:
    text = f"{headline} {summary}".lower()
    keyword_bonus = 0.0
    if "unexpected" in text or "surprise" in text:
        keyword_bonus += 0.4
    if "executive order" in text or "announces" in text or "threatens" in text:
        keyword_bonus += 0.3
    return min(1.0, abs(surprise_score) * 0.6 + keyword_bonus)


def _infer_trade_score(
    *,
    instrument_scope: tuple[str, ...],
    headline: str,
    summary: str,
    surprise_score: float,
    sentiment_score: float,
    confidence_score: float,
) -> float:
    direction_score = _infer_direction_score(
        instrument_scope=instrument_scope,
        headline=headline,
        summary=summary,
        sentiment_score=sentiment_score,
    )
    magnitude_score = _infer_magnitude_score(
        headline=headline,
        summary=summary,
        surprise_score=surprise_score,
    )
    unexpectedness_score = _infer_unexpectedness_score(
        headline=headline,
        summary=summary,
        surprise_score=surprise_score,
    )
    if any(symbol in instrument_scope for symbol in OIL_PROXY_SYMBOLS):
        return round(
            min(
                1.0,
                (abs(direction_score) * 0.45)
                + (magnitude_score * 0.25)
                + (unexpectedness_score * 0.20)
                + (confidence_score * 0.10),
            ),
            4,
        )
    return round(
        min(
            1.0,
            (abs(surprise_score) * 0.45)
            + (abs(sentiment_score) * 0.20)
            + (confidence_score * 0.20)
            + (unexpectedness_score * 0.15),
        ),
        4,
    )


def _coerce_datetime(raw_value: Any) -> datetime | None:
    if raw_value in {None, ""}:
        return None
    if isinstance(raw_value, (int, float)):
        timestamp = float(raw_value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(raw_value, str):
        normalized = raw_value.strip().replace("Z", "+00:00")
        if normalized.isdigit():
            return _coerce_datetime(int(normalized))
        if "T" not in normalized and "+" not in normalized:
            normalized = f"{normalized}T00:00:00+00:00"
        return datetime.fromisoformat(normalized)
    return None


def _coerce_float(raw_value: Any) -> float | None:
    if raw_value in {None, ""}:
        return None
    return float(raw_value)


def _coerce_surprise(
    item: dict[str, Any],
    actual_value: float | None,
    expected_value: float | None,
) -> float | None:
    raw_surprise = item.get("surprisePercent") or item.get("surprise") or item.get("surprise_score")
    if raw_surprise not in {None, ""}:
        value = float(raw_surprise)
        if abs(value) > 1:
            return value / 100
        return value
    if actual_value is None or expected_value in {None, 0}:
        return None
    return (actual_value - expected_value) / abs(expected_value)
