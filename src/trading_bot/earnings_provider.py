from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

import requests

from trading_bot.domain import (
    AssetClass,
    Bar,
    ConsensusSnapshot,
    EarningsCandidate,
    EarningsRelease,
    Instrument,
    PreEarningsAnalysis,
)
from trading_bot.persistence import EarningsDatabase


US_OTC_MICS = {"OOTC", "OTCM", "OTCQ", "OTCB", "PINK"}
EXCLUDED_DESCRIPTION_KEYWORDS = (
    " ETF",
    " ETN",
    " FUND",
    " TRUST",
    " ADR",
    " WARRANT",
    " RIGHTS",
    " UNIT",
    " ACQUISITION",
    " SPAC",
)


@dataclass(frozen=True)
class FinnhubSecurity:
    symbol: str
    description: str
    mic: str
    security_type: str


class FinnhubEarningsClient:
    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str, session: requests.Session | None = None) -> None:
        self.api_key = api_key
        self.session = session or requests.Session()
        self._us_common_stock_cache: dict[str, FinnhubSecurity] | None = None

    def fetch_earnings_calendar(
        self,
        *,
        from_date: date,
        to_date: date,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        payload = self._get(
            "/calendar/earnings",
            {
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                **({"symbol": symbol} if symbol else {}),
            },
            default={"earningsCalendar": []},
        )
        items = payload.get("earningsCalendar", payload) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    def fetch_profile(self, symbol: str) -> dict[str, Any]:
        payload = self._get("/stock/profile2", {"symbol": symbol}, default={})
        return payload if isinstance(payload, dict) else {}

    def fetch_eps_estimates(self, symbol: str) -> list[dict[str, Any]] | None:
        return self._optional_list("/stock/eps-estimate", {"symbol": symbol, "freq": "quarterly"})

    def fetch_revenue_estimates(self, symbol: str) -> list[dict[str, Any]] | None:
        return self._optional_list("/stock/revenue-estimate", {"symbol": symbol, "freq": "quarterly"})

    def fetch_eps_surprise_history(self, symbol: str, limit: int = 4) -> list[dict[str, Any]]:
        payload = self._get("/stock/earnings", {"symbol": symbol, "limit": str(limit)}, default=[])
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def fetch_us_common_stocks(self) -> dict[str, FinnhubSecurity]:
        if self._us_common_stock_cache is not None:
            return self._us_common_stock_cache
        payload = self._get(
            "/stock/symbol",
            {
                "exchange": "US",
                "securityType": "Common Stock",
            },
            default=[],
        )
        if not isinstance(payload, list):
            self._us_common_stock_cache = {}
            return self._us_common_stock_cache
        metadata: dict[str, FinnhubSecurity] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            metadata[symbol] = FinnhubSecurity(
                symbol=symbol,
                description=str(item.get("description", "")),
                mic=str(item.get("mic", "")).upper(),
                security_type=str(item.get("type", "")),
            )
        self._us_common_stock_cache = metadata
        return metadata

    def _optional_list(self, path: str, params: dict[str, str]) -> list[dict[str, Any]] | None:
        response = self.session.get(
            f"{self.BASE_URL}{path}",
            params={**params, "token": self.api_key},
            timeout=15,
        )
        if response.status_code == 403:
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if not isinstance(data, list):
            return None
        return [item for item in data if isinstance(item, dict)]

    def _get(self, path: str, params: dict[str, str], *, default: object | None = None) -> object:
        try:
            response = self.session.get(
                f"{self.BASE_URL}{path}",
                params={**params, "token": self.api_key},
                timeout=15,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            if default is not None:
                return default
            raise


class SecEdgarClient:
    def __init__(
        self,
        *,
        user_agent: str,
        session: requests.Session | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", user_agent)
        self._ticker_map: dict[str, str] | None = None
        self._freshness_cache: dict[tuple[str, date], float] = {}

    def get_cik_for_symbol(self, symbol: str) -> str | None:
        if self._ticker_map is None:
            try:
                response = self.session.get("https://www.sec.gov/files/company_tickers.json", timeout=20)
                response.raise_for_status()
                payload = response.json()
            except requests.RequestException:
                self._ticker_map = {}
                return None
            ticker_map: dict[str, str] = {}
            if isinstance(payload, dict):
                for item in payload.values():
                    if not isinstance(item, dict):
                        continue
                    ticker = str(item.get("ticker", "")).strip().upper()
                    cik_value = item.get("cik_str")
                    if not ticker or cik_value in {None, ""}:
                        continue
                    ticker_map[ticker] = str(int(cik_value)).zfill(10)
            self._ticker_map = ticker_map
        return self._ticker_map.get(symbol.upper())

    def filing_freshness_score(self, symbol: str, as_of: datetime) -> float:
        cache_key = (symbol.upper(), as_of.date())
        if cache_key in self._freshness_cache:
            return self._freshness_cache[cache_key]
        cik = self.get_cik_for_symbol(symbol)
        if cik is None:
            self._freshness_cache[cache_key] = 0.0
            return 0.0
        try:
            response = self.session.get(f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=20)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            self._freshness_cache[cache_key] = 0.0
            return 0.0
        recent = payload.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        strongest_score = 0.0
        for form, filed_at in zip(forms, dates):
            try:
                filing_date = date.fromisoformat(str(filed_at))
            except ValueError:
                continue
            age_days = (as_of.date() - filing_date).days
            if age_days < 0 or age_days > 45:
                continue
            base_weight = 1.0
            if form == "8-K":
                base_weight = 1.0
            elif form in {"10-Q", "10-K"}:
                base_weight = 0.9
            else:
                base_weight = 0.6
            freshness = max(0.0, 1 - (age_days / 45))
            strongest_score = max(strongest_score, 100 * base_weight * freshness)
        self._freshness_cache[cache_key] = strongest_score
        return strongest_score


class EarningsUniverseScanner:
    def __init__(
        self,
        *,
        broker,
        finnhub_api_key: str,
        sec_user_agent: str,
        database: EarningsDatabase | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.broker = broker
        self.database = database
        self.finnhub = FinnhubEarningsClient(finnhub_api_key, session=session)
        self.sec = SecEdgarClient(user_agent=sec_user_agent, session=session)

    def scan(self, config, *, as_of: datetime) -> list[PreEarningsAnalysis]:
        calendar_items = self.finnhub.fetch_earnings_calendar(
            from_date=as_of.date(),
            to_date=(as_of + timedelta(days=config.earnings_lookahead_days)).date(),
        )
        if not calendar_items:
            return []

        tradable_assets = {
            asset.symbol.upper(): asset
            for asset in self.broker.list_active_tradable_us_equities()
            if getattr(asset, "tradable", False)
        }
        common_stock_meta = self.finnhub.fetch_us_common_stocks()

        raw_symbols = sorted(
            {
                str(item.get("symbol", "")).strip().upper()
                for item in calendar_items
                if _optional_float(item.get("epsEstimate")) is not None
                and _optional_float(item.get("revenueEstimate")) is not None
            }
        )
        eligible_symbols = [
            symbol
            for symbol in raw_symbols
            if symbol in tradable_assets and _is_supported_common_stock(common_stock_meta.get(symbol))
        ]
        if not eligible_symbols:
            return []

        latest_prices = self._fetch_latest_prices(eligible_symbols)
        daily_bars = self._fetch_daily_bars(eligible_symbols, as_of=as_of)
        analyses: list[PreEarningsAnalysis] = []

        for item in calendar_items:
            symbol = str(item.get("symbol", "")).strip().upper()
            if symbol not in latest_prices or symbol not in daily_bars:
                continue
            security = common_stock_meta.get(symbol)
            if security is None:
                continue
            latest_price = latest_prices[symbol]
            bars = daily_bars[symbol]
            avg_dollar_volume = _average_dollar_volume(bars, window=30)
            profile = self.finnhub.fetch_profile(symbol)
            market_cap_raw = _optional_float(profile.get("marketCapitalization"))
            if market_cap_raw is None:
                continue
            market_cap_usd = market_cap_raw * 1_000_000
            if not _passes_universe_filters(
                security=security,
                latest_price=latest_price,
                market_cap_usd=market_cap_usd,
                avg_dollar_volume_usd=avg_dollar_volume,
                config=config,
            ):
                continue

            earnings_date = date.fromisoformat(str(item["date"]))
            consensus = ConsensusSnapshot(
                symbol=symbol,
                period=str(item["date"]),
                captured_at=as_of,
                eps_estimate=float(item["epsEstimate"]),
                revenue_estimate=float(item["revenueEstimate"]),
                quarter=_optional_int(item.get("quarter")),
                year=_optional_int(item.get("year")),
                eps_actual=_optional_float(item.get("epsActual")),
                revenue_actual=_optional_float(item.get("revenueActual")),
                source="finnhub_calendar",
            )
            previous_consensus = None
            if self.database is not None:
                previous_consensus = self.database.get_previous_consensus(
                    symbol=symbol,
                    period=consensus.period,
                    before=as_of,
                    lookback_days=30,
                )
            eps_revision_score = _estimate_revision_score(
                current_value=consensus.eps_estimate,
                previous_value=previous_consensus.eps_estimate if previous_consensus else None,
            )
            revenue_revision_score = _estimate_revision_score(
                current_value=consensus.revenue_estimate,
                previous_value=previous_consensus.revenue_estimate if previous_consensus else None,
            )
            surprise_quality_score = self._surprise_quality_score(symbol=symbol, as_of=as_of)
            filing_freshness_score = self.sec.filing_freshness_score(symbol, as_of)
            liquidity_volatility_score = _liquidity_volatility_score(
                avg_dollar_volume_usd=avg_dollar_volume,
                latest_price=latest_price,
                bars=bars,
                minimum_avg_dollar_volume_usd=config.earnings_min_avg_dollar_volume_usd,
            )

            candidate = EarningsCandidate(
                symbol=symbol,
                earnings_date=earnings_date,
                earnings_hour=_normalize_hour(item.get("hour")),
                instrument=Instrument(symbol=symbol, asset_class=AssetClass.STOCK),
                last_price=latest_price,
                market_cap_usd=market_cap_usd,
                avg_dollar_volume_usd=avg_dollar_volume,
                exchange=str(profile.get("exchange") or getattr(tradable_assets[symbol], "exchange", "")),
                mic=security.mic,
                company_name=str(profile.get("name") or security.description),
                industry=_optional_str(profile.get("finnhubIndustry")),
                eps_estimate=consensus.eps_estimate,
                revenue_estimate=consensus.revenue_estimate,
                extended_hours_eligible=True,
            )
            score = (
                (0.30 * eps_revision_score)
                + (0.20 * revenue_revision_score)
                + (0.20 * surprise_quality_score)
                + (0.15 * filing_freshness_score)
                + (0.15 * liquidity_volatility_score)
            )
            analysis = PreEarningsAnalysis(
                candidate=candidate,
                analysis_at=as_of,
                score=round(score, 2),
                eps_revision_score=round(eps_revision_score, 2),
                revenue_revision_score=round(revenue_revision_score, 2),
                surprise_quality_score=round(surprise_quality_score, 2),
                filing_freshness_score=round(filing_freshness_score, 2),
                liquidity_volatility_score=round(liquidity_volatility_score, 2),
                reasons=_build_reason_lines(
                    candidate=candidate,
                    score=score,
                    eps_revision_score=eps_revision_score,
                    revenue_revision_score=revenue_revision_score,
                    surprise_quality_score=surprise_quality_score,
                    filing_freshness_score=filing_freshness_score,
                    liquidity_volatility_score=liquidity_volatility_score,
                ),
                consensus=consensus,
            )
            analyses.append(analysis)

        analyses.sort(
            key=lambda item: (
                item.score,
                item.candidate.avg_dollar_volume_usd,
                -item.candidate.earnings_date.toordinal(),
            ),
            reverse=True,
        )
        analyses = analyses[: config.earnings_universe_max_size]
        if self.database is not None:
            self.database.store_scan(analyses)
        return analyses

    def fetch_live_releases(
        self,
        *,
        symbols: set[str],
        now: datetime,
    ) -> dict[str, EarningsRelease]:
        if not symbols:
            return {}
        items = self.finnhub.fetch_earnings_calendar(
            from_date=(now - timedelta(days=1)).date(),
            to_date=(now + timedelta(days=1)).date(),
        )
        releases: dict[str, EarningsRelease] = {}
        for item in items:
            symbol = str(item.get("symbol", "")).strip().upper()
            if symbol not in symbols:
                continue
            eps_actual = _optional_float(item.get("epsActual"))
            eps_estimate = _optional_float(item.get("epsEstimate"))
            revenue_actual = _optional_float(item.get("revenueActual"))
            revenue_estimate = _optional_float(item.get("revenueEstimate"))
            if None in {eps_actual, eps_estimate, revenue_actual, revenue_estimate}:
                continue
            earnings_date = date.fromisoformat(str(item["date"]))
            event_id = _earnings_event_id(symbol, earnings_date, item.get("quarter"), item.get("year"))
            releases[symbol] = EarningsRelease(
                event_id=event_id,
                symbol=symbol,
                earnings_date=earnings_date,
                observed_at=now,
                published_at=_derive_release_timestamp(
                    earnings_date=earnings_date,
                    hour=_normalize_hour(item.get("hour")),
                    fallback=now,
                ),
                hour=_normalize_hour(item.get("hour")),
                quarter=_optional_int(item.get("quarter")),
                year=_optional_int(item.get("year")),
                eps_actual=eps_actual,
                eps_estimate=eps_estimate,
                revenue_actual=revenue_actual,
                revenue_estimate=revenue_estimate,
                eps_surprise_pct=_surprise_pct(eps_actual, eps_estimate),
                revenue_surprise_pct=_surprise_pct(revenue_actual, revenue_estimate),
                anchor_price=None,
                source="finnhub_calendar",
                in_universe=True,
                extended_hours_eligible=True,
            )
        return releases

    def fetch_historical_releases(
        self,
        *,
        symbols: set[str],
        from_date: date,
        to_date: date,
    ) -> list[EarningsRelease]:
        if not symbols:
            return []
        items = self.finnhub.fetch_earnings_calendar(from_date=from_date, to_date=to_date)
        releases: list[EarningsRelease] = []
        for item in items:
            symbol = str(item.get("symbol", "")).strip().upper()
            if symbol not in symbols:
                continue
            eps_actual = _optional_float(item.get("epsActual"))
            eps_estimate = _optional_float(item.get("epsEstimate"))
            revenue_actual = _optional_float(item.get("revenueActual"))
            revenue_estimate = _optional_float(item.get("revenueEstimate"))
            if None in {eps_actual, eps_estimate, revenue_actual, revenue_estimate}:
                continue
            earnings_date = date.fromisoformat(str(item["date"]))
            published_at = _derive_release_timestamp(
                earnings_date=earnings_date,
                hour=_normalize_hour(item.get("hour")),
                fallback=datetime.combine(earnings_date, time(12, 0), tzinfo=timezone.utc),
            )
            releases.append(
                EarningsRelease(
                    event_id=_earnings_event_id(symbol, earnings_date, item.get("quarter"), item.get("year")),
                    symbol=symbol,
                    earnings_date=earnings_date,
                    observed_at=published_at,
                    published_at=published_at,
                    hour=_normalize_hour(item.get("hour")),
                    quarter=_optional_int(item.get("quarter")),
                    year=_optional_int(item.get("year")),
                    eps_actual=eps_actual,
                    eps_estimate=eps_estimate,
                    revenue_actual=revenue_actual,
                    revenue_estimate=revenue_estimate,
                    eps_surprise_pct=_surprise_pct(eps_actual, eps_estimate),
                    revenue_surprise_pct=_surprise_pct(revenue_actual, revenue_estimate),
                    anchor_price=None,
                    source="finnhub_calendar",
                    in_universe=True,
                    extended_hours_eligible=True,
                )
            )
        releases.sort(key=lambda item: item.observed_at)
        return releases

    def _fetch_latest_prices(self, symbols: list[str]) -> dict[str, float]:
        prices: dict[str, float] = {}
        for chunk in _chunked(symbols, 200):
            latest_trades = self.broker.get_latest_stock_trades(chunk)
            for symbol, trade in latest_trades.items():
                price = _optional_float(getattr(trade, "price", None))
                if price is not None:
                    prices[symbol.upper()] = price
        return prices

    def _fetch_daily_bars(self, symbols: list[str], *, as_of: datetime) -> dict[str, list[Bar]]:
        from alpaca.data.timeframe import TimeFrame

        bars_by_symbol: dict[str, list[Bar]] = {}
        start = as_of - timedelta(days=45)
        for chunk in _chunked(symbols, 200):
            response = self.broker.get_historical_stock_bars_batch(
                chunk,
                start=start,
                end=as_of,
                timeframe=TimeFrame.Day,
            )
            for symbol, bars in response.items():
                if bars:
                    bars_by_symbol[symbol.upper()] = bars
        return bars_by_symbol

    def _surprise_quality_score(self, *, symbol: str, as_of: datetime) -> float:
        history = self.finnhub.fetch_earnings_calendar(
            from_date=(as_of - timedelta(days=500)).date(),
            to_date=as_of.date(),
            symbol=symbol,
        )
        quarterly_scores: list[float] = []
        for item in history:
            eps_actual = _optional_float(item.get("epsActual"))
            eps_estimate = _optional_float(item.get("epsEstimate"))
            revenue_actual = _optional_float(item.get("revenueActual"))
            revenue_estimate = _optional_float(item.get("revenueEstimate"))
            if None not in {eps_actual, eps_estimate, revenue_actual, revenue_estimate}:
                quarterly_scores.append(
                    _quarter_surprise_quality_score(
                        eps_surprise_pct=_surprise_pct(eps_actual, eps_estimate),
                        revenue_surprise_pct=_surprise_pct(revenue_actual, revenue_estimate),
                    )
                )
            if len(quarterly_scores) >= 4:
                break
        if quarterly_scores:
            return float(median(quarterly_scores))

        eps_history = self.finnhub.fetch_eps_surprise_history(symbol, limit=4)
        eps_scores = [
            max(
                0.0,
                min(
                    100.0,
                    50 + ((_optional_float(item.get("surprisePercent")) or 0.0) * 2),
                ),
            )
            for item in eps_history
        ]
        if not eps_scores:
            return 0.0
        return float(median(eps_scores))


def _passes_universe_filters(
    *,
    security: FinnhubSecurity,
    latest_price: float,
    market_cap_usd: float,
    avg_dollar_volume_usd: float,
    config,
) -> bool:
    if latest_price < config.earnings_min_price_usd:
        return False
    if market_cap_usd < config.earnings_market_cap_min_usd:
        return False
    if market_cap_usd > config.earnings_market_cap_max_usd:
        return False
    if avg_dollar_volume_usd < config.earnings_min_avg_dollar_volume_usd:
        return False
    if security.mic in US_OTC_MICS:
        return False
    upper_description = security.description.upper()
    return not any(keyword in upper_description for keyword in EXCLUDED_DESCRIPTION_KEYWORDS)


def _is_supported_common_stock(security: FinnhubSecurity | None) -> bool:
    if security is None:
        return False
    return security.security_type.lower() == "common stock"


def _estimate_revision_score(*, current_value: float, previous_value: float | None) -> float:
    if previous_value in {None, 0}:
        return 0.0
    delta_pct = (current_value - previous_value) / abs(previous_value)
    return max(0.0, min(100.0, 50 + (delta_pct * 250)))


def _quarter_surprise_quality_score(*, eps_surprise_pct: float, revenue_surprise_pct: float) -> float:
    eps_component = max(-1.0, min(1.0, eps_surprise_pct / 0.15))
    revenue_component = max(-1.0, min(1.0, revenue_surprise_pct / 0.06))
    raw = (0.7 * eps_component) + (0.3 * revenue_component)
    return max(0.0, min(100.0, 50 + (raw * 50)))


def _liquidity_volatility_score(
    *,
    avg_dollar_volume_usd: float,
    latest_price: float,
    bars: list[Bar],
    minimum_avg_dollar_volume_usd: float,
) -> float:
    volume_score = min(100.0, (avg_dollar_volume_usd / minimum_avg_dollar_volume_usd) * 40)
    if latest_price <= 0 or len(bars) < 15:
        return volume_score
    atr_values: list[float] = []
    for current, previous in zip(bars[-14:], bars[-15:-1]):
        atr_values.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    atr_pct = (sum(atr_values) / len(atr_values)) / latest_price if atr_values else 0.0
    if atr_pct <= 0:
        return volume_score
    if 0.02 <= atr_pct <= 0.08:
        volatility_score = 60.0
    elif 0.01 <= atr_pct <= 0.12:
        volatility_score = 40.0
    else:
        volatility_score = 20.0
    return min(100.0, volume_score + volatility_score)


def _average_dollar_volume(bars: list[Bar], *, window: int) -> float:
    trailing = [bar.close * bar.volume for bar in bars[-window:] if bar.volume > 0]
    if not trailing:
        return 0.0
    return sum(trailing) / len(trailing)


def _build_reason_lines(
    *,
    candidate: EarningsCandidate,
    score: float,
    eps_revision_score: float,
    revenue_revision_score: float,
    surprise_quality_score: float,
    filing_freshness_score: float,
    liquidity_volatility_score: float,
) -> tuple[str, ...]:
    reasons = [
        f"score {score:.1f}",
        f"surprise-historikk {surprise_quality_score:.1f}",
        f"SEC freshness {filing_freshness_score:.1f}",
        f"likviditet/volatilitet {liquidity_volatility_score:.1f}",
    ]
    if eps_revision_score > 0:
        reasons.append(f"EPS-revisjon {eps_revision_score:.1f}")
    if revenue_revision_score > 0:
        reasons.append(f"Revenue-revisjon {revenue_revision_score:.1f}")
    reasons.append(
        f"{candidate.earnings_date.isoformat()} {candidate.earnings_hour or 'tbd'} EPS {candidate.eps_estimate:.2f}"
    )
    return tuple(reasons[:5])


def _derive_release_timestamp(
    *,
    earnings_date: date,
    hour: str | None,
    fallback: datetime,
) -> datetime:
    eastern = ZoneInfo("America/New_York")
    if hour == "bmo":
        naive_time = time(8, 0)
    elif hour == "amc":
        naive_time = time(16, 5)
    else:
        naive_time = time(12, 0)
    return datetime.combine(earnings_date, naive_time, tzinfo=eastern).astimezone(timezone.utc)


def _earnings_event_id(symbol: str, earnings_date: date, quarter: Any, year: Any) -> str:
    quarter_value = _optional_int(quarter)
    year_value = _optional_int(year)
    return f"earnings-{symbol}-{earnings_date.isoformat()}-{quarter_value or 'na'}-{year_value or 'na'}"


def _surprise_pct(actual: float, expected: float) -> float:
    if expected == 0:
        return 0.0
    return (actual - expected) / abs(expected)


def _optional_float(raw_value: Any) -> float | None:
    if raw_value in {None, ""}:
        return None
    return float(raw_value)


def _optional_int(raw_value: Any) -> int | None:
    if raw_value in {None, ""}:
        return None
    return int(raw_value)


def _optional_str(raw_value: Any) -> str | None:
    if raw_value in {None, ""}:
        return None
    return str(raw_value)


def _normalize_hour(raw_value: Any) -> str | None:
    if raw_value in {None, ""}:
        return None
    value = str(raw_value).strip().lower()
    if value in {"bmo", "amc"}:
        return value
    return value


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]
