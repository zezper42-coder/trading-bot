from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from trading_bot.domain import Instrument, StructuredEvent, canonical_symbol, symbol_in_scope
from trading_bot.webhook_bridge import normalize_news_items


@dataclass(frozen=True)
class RssFeedDefinition:
    name: str
    url: str


class OfficialRssStructuredEventFeed:
    def __init__(
        self,
        feeds: tuple[RssFeedDefinition, ...],
        *,
        session: requests.Session | None = None,
        timeout_seconds: int = 10,
        user_agent: str = "trading-bot/0.1",
    ) -> None:
        self.feeds = feeds
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", user_agent)

    def get_recent_structured_events(
        self,
        instrument: Instrument,
        since: datetime,
        until: datetime,
    ) -> tuple[StructuredEvent, ...]:
        events: list[StructuredEvent] = []
        for feed in self.feeds:
            response = self.session.get(feed.url, timeout=self.timeout_seconds)
            response.raise_for_status()
            items = _parse_rss_items(response.text, feed.name)
            events.extend(
                normalize_news_items(
                    items,
                    received_at=until,
                    envelope_event="rss",
                    event_id_namespace=f"rss-{feed.name}",
                    default_source=feed.name,
                )
            )
        filtered = _filter_events(events, instrument, since, until)
        return tuple(filtered)


class SecCompanySubmissionsFeed:
    IMPORTANT_FORMS = {"8-K", "10-Q", "10-K", "6-K", "SC 13D", "SC 13G"}

    def __init__(
        self,
        company_map: dict[str, str],
        *,
        session: requests.Session | None = None,
        timeout_seconds: int = 10,
        user_agent: str = "trading-bot/0.1",
    ) -> None:
        self.company_map = {canonical_symbol(symbol): cik for symbol, cik in company_map.items()}
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", user_agent)

    def get_recent_structured_events(
        self,
        instrument: Instrument,
        since: datetime,
        until: datetime,
    ) -> tuple[StructuredEvent, ...]:
        cik = self.company_map.get(canonical_symbol(instrument.symbol))
        if cik is None:
            return ()

        response = self.session.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        items = _parse_sec_submission_items(payload, instrument.symbol)
        events = normalize_news_items(
            items,
            received_at=until,
            envelope_event="sec-submissions",
            event_id_namespace=f"sec-submissions-{canonical_symbol(instrument.symbol).lower()}",
            default_source="sec_submissions",
        )
        filtered = _filter_events(events, instrument, since, until)
        return tuple(filtered)


def _parse_rss_items(xml_text: str, source_name: str) -> tuple[dict[str, Any], ...]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, Any]] = []
    namespaces = {
        "atom": "http://www.w3.org/2005/Atom",
        "content": "http://purl.org/rss/1.0/modules/content/",
    }

    for item in root.findall(".//item"):
        title = _xml_text(item.find("title"))
        description = _xml_text(item.find("description")) or _xml_text(item.find("content:encoded", namespaces))
        guid = _xml_text(item.find("guid")) or _xml_text(item.find("link"))
        published = _parse_rss_datetime(
            _xml_text(item.find("pubDate")) or _xml_text(item.find("published")) or _xml_text(item.find("updated"))
        )
        if title is None:
            continue
        items.append(
            {
                "id": guid or title,
                "headline": title,
                "summary": description or "",
                "datetime": published.isoformat() if published is not None else "",
                "source": source_name,
            }
        )

    for entry in root.findall(".//atom:entry", namespaces):
        title = _xml_text(entry.find("atom:title", namespaces))
        summary = _xml_text(entry.find("atom:summary", namespaces)) or _xml_text(entry.find("atom:content", namespaces))
        entry_id = _xml_text(entry.find("atom:id", namespaces)) or title
        published = _parse_rss_datetime(
            _xml_text(entry.find("atom:published", namespaces)) or _xml_text(entry.find("atom:updated", namespaces))
        )
        if title is None:
            continue
        items.append(
            {
                "id": entry_id or title,
                "headline": title,
                "summary": summary or "",
                "datetime": published.isoformat() if published is not None else "",
                "source": source_name,
            }
        )
    return tuple(items)


def _parse_sec_submission_items(payload: dict[str, Any], symbol: str) -> tuple[dict[str, Any], ...]:
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_doc_descriptions = recent.get("primaryDocDescription", [])

    items: list[dict[str, Any]] = []
    for index, form in enumerate(forms):
        if form not in SecCompanySubmissionsFeed.IMPORTANT_FORMS:
            continue
        filing_date = filing_dates[index] if index < len(filing_dates) else None
        description = primary_doc_descriptions[index] if index < len(primary_doc_descriptions) else ""
        accession_number = accession_numbers[index] if index < len(accession_numbers) else f"{symbol}-{index}"
        items.append(
            {
                "id": accession_number,
                "headline": f"{symbol} {form} filing",
                "summary": description or f"SEC {form} submission for {symbol}",
                "datetime": filing_date or "",
                "related": symbol,
                "source": "sec_submissions",
                # Low positive baseline; these are mainly corroboration sources.
                "surprise_score": 0.4 if form == "8-K" else 0.25,
                "sentiment_score": 0.12 if form == "8-K" else 0.05,
                "confidence_score": 0.92,
            }
        )
    return tuple(items)


def _filter_events(
    events: list[StructuredEvent] | tuple[StructuredEvent, ...],
    instrument: Instrument,
    since: datetime,
    until: datetime,
) -> list[StructuredEvent]:
    filtered: list[StructuredEvent] = []
    for event in events:
        if event.published_at < since or event.published_at > until:
            continue
        if not symbol_in_scope(instrument.symbol, event.instrument_scope):
            continue
        filtered.append(event)
    filtered.sort(key=lambda event: event.published_at, reverse=True)
    return filtered


def _xml_text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    return element.text.strip()


def _parse_rss_datetime(raw_value: str | None) -> datetime | None:
    if raw_value is None or not raw_value.strip():
        return None
    try:
        parsed = parsedate_to_datetime(raw_value)
    except (TypeError, ValueError, IndexError):
        normalized = raw_value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
