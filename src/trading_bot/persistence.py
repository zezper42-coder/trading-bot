from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from trading_bot.domain import (
    AssetClass,
    ConsensusSnapshot,
    EarningsCandidate,
    EarningsRelease,
    Instrument,
    PreEarningsAnalysis,
)


class JsonlTradeLogger:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.path is None:
            return
        record = {"event_type": event_type, **_serialize(payload)}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")


class EarningsDatabase:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def store_scan(self, analyses: list[PreEarningsAnalysis]) -> None:
        if self.path is None or not analyses:
            return
        with self._connect() as connection:
            for analysis in analyses:
                candidate = analysis.candidate
                consensus = analysis.consensus
                connection.execute(
                    """
                    INSERT INTO universe_snapshots (
                        snapshot_at, symbol, earnings_date, earnings_hour, last_price, market_cap_usd,
                        avg_dollar_volume_usd, exchange, mic, company_name, industry,
                        eps_estimate, revenue_estimate, extended_hours_eligible
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        analysis.analysis_at.isoformat(),
                        candidate.symbol,
                        candidate.earnings_date.isoformat(),
                        candidate.earnings_hour,
                        candidate.last_price,
                        candidate.market_cap_usd,
                        candidate.avg_dollar_volume_usd,
                        candidate.exchange,
                        candidate.mic,
                        candidate.company_name,
                        candidate.industry,
                        candidate.eps_estimate,
                        candidate.revenue_estimate,
                        int(candidate.extended_hours_eligible),
                    ),
                )
                if consensus is not None:
                    connection.execute(
                        """
                        INSERT INTO consensus_snapshots (
                            captured_at, symbol, period, quarter, year, eps_estimate, revenue_estimate,
                            eps_actual, revenue_actual, number_analysts_eps, number_analysts_revenue, source
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            consensus.captured_at.isoformat(),
                            consensus.symbol,
                            consensus.period,
                            consensus.quarter,
                            consensus.year,
                            consensus.eps_estimate,
                            consensus.revenue_estimate,
                            consensus.eps_actual,
                            consensus.revenue_actual,
                            consensus.number_analysts_eps,
                            consensus.number_analysts_revenue,
                            consensus.source,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO pre_earnings_analyses (
                        analysis_at, symbol, earnings_date, score, eps_revision_score, revenue_revision_score,
                        surprise_quality_score, filing_freshness_score, liquidity_volatility_score, reasons
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        analysis.analysis_at.isoformat(),
                        candidate.symbol,
                        candidate.earnings_date.isoformat(),
                        analysis.score,
                        analysis.eps_revision_score,
                        analysis.revenue_revision_score,
                        analysis.surprise_quality_score,
                        analysis.filing_freshness_score,
                        analysis.liquidity_volatility_score,
                        json.dumps(list(analysis.reasons), ensure_ascii=True),
                    ),
                )

    def get_previous_consensus(
        self,
        *,
        symbol: str,
        period: str,
        before: datetime,
        lookback_days: int = 30,
    ) -> ConsensusSnapshot | None:
        if self.path is None:
            return None
        lower_bound = before.timestamp() - (lookback_days * 86_400)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT captured_at, symbol, period, quarter, year, eps_estimate, revenue_estimate,
                       eps_actual, revenue_actual, number_analysts_eps, number_analysts_revenue, source
                FROM consensus_snapshots
                WHERE symbol = ? AND period = ? AND unixepoch(captured_at) < unixepoch(?) AND unixepoch(captured_at) >= ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (symbol, period, before.isoformat(), lower_bound),
            ).fetchone()
        if row is None:
            return None
        return ConsensusSnapshot(
            captured_at=datetime.fromisoformat(row[0]),
            symbol=row[1],
            period=row[2],
            quarter=row[3],
            year=row[4],
            eps_estimate=row[5],
            revenue_estimate=row[6],
            eps_actual=row[7],
            revenue_actual=row[8],
            number_analysts_eps=row[9],
            number_analysts_revenue=row[10],
            source=row[11],
        )

    def get_release(self, event_id: str) -> EarningsRelease | None:
        if self.path is None:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT event_id, symbol, earnings_date, observed_at, published_at, hour, quarter, year,
                       eps_actual, eps_estimate, revenue_actual, revenue_estimate, eps_surprise_pct,
                       revenue_surprise_pct, anchor_price, source, in_universe, extended_hours_eligible
                FROM earnings_releases
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return EarningsRelease(
            event_id=row[0],
            symbol=row[1],
            earnings_date=date.fromisoformat(row[2]),
            observed_at=datetime.fromisoformat(row[3]),
            published_at=datetime.fromisoformat(row[4]),
            hour=row[5],
            quarter=row[6],
            year=row[7],
            eps_actual=row[8],
            eps_estimate=row[9],
            revenue_actual=row[10],
            revenue_estimate=row[11],
            eps_surprise_pct=row[12],
            revenue_surprise_pct=row[13],
            anchor_price=row[14],
            source=row[15],
            in_universe=bool(row[16]),
            extended_hours_eligible=bool(row[17]),
        )

    def store_release(self, release: EarningsRelease) -> None:
        if self.path is None:
            return
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO earnings_releases (
                    event_id, symbol, earnings_date, observed_at, published_at, hour, quarter, year,
                    eps_actual, eps_estimate, revenue_actual, revenue_estimate, eps_surprise_pct,
                    revenue_surprise_pct, anchor_price, source, in_universe, extended_hours_eligible
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    release.event_id,
                    release.symbol,
                    release.earnings_date.isoformat(),
                    release.observed_at.isoformat(),
                    release.published_at.isoformat(),
                    release.hour,
                    release.quarter,
                    release.year,
                    release.eps_actual,
                    release.eps_estimate,
                    release.revenue_actual,
                    release.revenue_estimate,
                    release.eps_surprise_pct,
                    release.revenue_surprise_pct,
                    release.anchor_price,
                    release.source,
                    int(release.in_universe),
                    int(release.extended_hours_eligible),
                ),
            )

    def log_trade(
        self,
        *,
        timestamp: datetime,
        event_id: str | None,
        symbol: str,
        action: str,
        price: float,
        qty: float | None,
        notional: float | None,
        order_id: str,
        reason: str,
        dry_run: bool,
        exit_reason: str | None,
    ) -> None:
        if self.path is None:
            return
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO earnings_trades (
                    timestamp, event_id, symbol, action, price, qty, notional, order_id, reason, dry_run, exit_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp.isoformat(),
                    event_id,
                    symbol,
                    action,
                    price,
                    qty,
                    notional,
                    order_id,
                    reason,
                    int(dry_run),
                    exit_reason,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        if self.path is None:
            raise RuntimeError("EarningsDatabase er deaktivert.")
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS universe_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    earnings_date TEXT NOT NULL,
                    earnings_hour TEXT,
                    last_price REAL NOT NULL,
                    market_cap_usd REAL NOT NULL,
                    avg_dollar_volume_usd REAL NOT NULL,
                    exchange TEXT NOT NULL,
                    mic TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    industry TEXT,
                    eps_estimate REAL NOT NULL,
                    revenue_estimate REAL NOT NULL,
                    extended_hours_eligible INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consensus_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    period TEXT NOT NULL,
                    quarter INTEGER,
                    year INTEGER,
                    eps_estimate REAL NOT NULL,
                    revenue_estimate REAL NOT NULL,
                    eps_actual REAL,
                    revenue_actual REAL,
                    number_analysts_eps INTEGER,
                    number_analysts_revenue INTEGER,
                    source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pre_earnings_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    earnings_date TEXT NOT NULL,
                    score REAL NOT NULL,
                    eps_revision_score REAL NOT NULL,
                    revenue_revision_score REAL NOT NULL,
                    surprise_quality_score REAL NOT NULL,
                    filing_freshness_score REAL NOT NULL,
                    liquidity_volatility_score REAL NOT NULL,
                    reasons TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS earnings_releases (
                    event_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    earnings_date TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    hour TEXT,
                    quarter INTEGER,
                    year INTEGER,
                    eps_actual REAL NOT NULL,
                    eps_estimate REAL NOT NULL,
                    revenue_actual REAL NOT NULL,
                    revenue_estimate REAL NOT NULL,
                    eps_surprise_pct REAL NOT NULL,
                    revenue_surprise_pct REAL NOT NULL,
                    anchor_price REAL,
                    source TEXT NOT NULL,
                    in_universe INTEGER NOT NULL,
                    extended_hours_eligible INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS earnings_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_id TEXT,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    price REAL NOT NULL,
                    qty REAL,
                    notional REAL,
                    order_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    dry_run INTEGER NOT NULL,
                    exit_reason TEXT
                );
                """
            )


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value):
        return _serialize(asdict(value))
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value
