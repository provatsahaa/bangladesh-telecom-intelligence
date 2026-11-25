"""Additive SQLite credit-usage monitor and query-frequency guard for Tavily API."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..discovery import DHAKA_TZ
from ..storage import SCHEMA_SQL as NEWS_SCHEMA_SQL, open_sqlite_connection

TAVILY_USAGE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tavily_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    called_at TEXT NOT NULL,
    month_key TEXT NOT NULL,           -- YYYY-MM
    endpoint TEXT NOT NULL,            -- search | extract
    query_id TEXT,
    item_count INTEGER NOT NULL DEFAULT 1,
    credits_used REAL NOT NULL DEFAULT 0.0,
    results_returned INTEGER NOT NULL DEFAULT 0,
    new_articles_saved INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,              -- OK | BUDGET_BLOCKED | ERROR
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_tavily_usage_month ON tavily_usage_log(month_key);

CREATE TABLE IF NOT EXISTS tavily_query_state (
    query_id TEXT PRIMARY KEY,
    last_run_at TEXT NOT NULL,
    last_results_count INTEGER NOT NULL DEFAULT 0,
    last_saved_count INTEGER NOT NULL DEFAULT 0
);
"""


def estimate_extract_credits(successful_url_count: int, extract_depth: str = "basic") -> float:
    """Tavily Extract charges 1 credit per 5 successful URLs (basic) or 2 per 5 (advanced)."""
    if successful_url_count <= 0:
        return 0.0
    rate = 2.0 if extract_depth == "advanced" else 1.0
    return float(math.ceil(successful_url_count / 5.0) * rate)


class TavilyUsageTracker:
    """Tracks Tavily API credit consumption and enforces monthly budget & query cooldowns."""

    def __init__(self, db_path: str | Path = "data/bd_telecom_news.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return open_sqlite_connection(self.db_path, timeout=20.0)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(NEWS_SCHEMA_SQL)
            conn.executescript(TAVILY_USAGE_SCHEMA_SQL)

    @staticmethod
    def _current_month_key() -> str:
        return datetime.now(DHAKA_TZ).strftime("%Y-%m")

    def get_monthly_credits_used(self, month_key: str | None = None) -> float:
        mk = month_key or self._current_month_key()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(credits_used), 0.0) FROM tavily_usage_log WHERE month_key = ?",
                (mk,),
            ).fetchone()
        return round(float(row[0] or 0.0), 2)

    def can_spend(self, credits_needed: float, monthly_budget: int) -> bool:
        return (self.get_monthly_credits_used() + credits_needed) <= float(monthly_budget)

    def is_query_due(self, query_id: str, min_interval_hours: float) -> bool:
        """Return True if `query_id` has never run or ran longer than `min_interval_hours` ago."""
        if min_interval_hours <= 0:
            return True
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_run_at FROM tavily_query_state WHERE query_id = ?",
                (query_id,),
            ).fetchone()
        if row is None or not row["last_run_at"]:
            return True
        last_dt = datetime.fromisoformat(row["last_run_at"])
        return datetime.now(DHAKA_TZ) >= (last_dt + timedelta(hours=min_interval_hours))

    def record_call(
        self,
        *,
        endpoint: str,
        credits_used: float,
        status: str,
        query_id: str | None = None,
        item_count: int = 1,
        results_returned: int = 0,
        new_articles_saved: int = 0,
        error_message: str | None = None,
    ) -> None:
        now = datetime.now(DHAKA_TZ)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tavily_usage_log (
                    called_at, month_key, endpoint, query_id, item_count,
                    credits_used, results_returned, new_articles_saved, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now.isoformat(),
                    now.strftime("%Y-%m"),
                    endpoint,
                    query_id,
                    item_count,
                    credits_used,
                    results_returned,
                    new_articles_saved,
                    status,
                    error_message[:400] if error_message else None,
                ),
            )

    def mark_query_ran(self, query_id: str, results_count: int, saved_count: int) -> None:
        now_iso = datetime.now(DHAKA_TZ).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tavily_query_state (query_id, last_run_at, last_results_count, last_saved_count)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(query_id) DO UPDATE SET
                    last_run_at = excluded.last_run_at,
                    last_results_count = excluded.last_results_count,
                    last_saved_count = excluded.last_saved_count
                """,
                (query_id, now_iso, results_count, saved_count),
            )

    def get_usage_summary(self, monthly_budget: int = 800) -> dict[str, Any]:
        mk = self._current_month_key()
        with self._connect() as conn:
            used = self.get_monthly_credits_used(mk)
            calls = conn.execute(
                """
                SELECT endpoint, COUNT(*) AS cnt, COALESCE(SUM(credits_used), 0.0) AS credits,
                       COALESCE(SUM(new_articles_saved), 0) AS saved
                FROM tavily_usage_log
                WHERE month_key = ?
                GROUP BY endpoint
                """,
                (mk,),
            ).fetchall()
            q_states = conn.execute(
                "SELECT * FROM tavily_query_state ORDER BY last_run_at DESC"
            ).fetchall()
        return {
            "month": mk,
            "credits_used": used,
            "monthly_budget": monthly_budget,
            "credits_remaining": round(max(0.0, monthly_budget - used), 2),
            "by_endpoint": [dict(r) for r in calls],
            "query_states": [dict(r) for r in q_states],
        }
