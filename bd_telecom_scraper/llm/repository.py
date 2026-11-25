"""Additive SQLite repository for AI article analysis, daily briefings, and job locks.

Shares `data/bd_telecom_news.db` with the existing scraper without altering
or duplicating the `articles` table.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psutil

from ..discovery import DHAKA_TZ
from ..storage import SCHEMA_SQL as NEWS_SCHEMA_SQL, open_sqlite_connection
from .schemas import ArticleIntelligence, DailyBriefingOutput

LLM_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS article_ai_analysis (
    article_id INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING | PROCESSING | COMPLETED | FAILED
    english_headline TEXT,
    summary TEXT,
    key_takeaways_json TEXT,
    main_category TEXT,
    subcategory TEXT,
    organizations_json TEXT,
    operators_json TEXT,
    sentiment TEXT,
    sentiment_confidence REAL,
    industry_impact TEXT,
    telecom_relevance TEXT,
    reported_facts_json TEXT,
    industry_implications TEXT,
    model_name TEXT,
    prompt_version TEXT,
    source_content_hash TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    processing_ms INTEGER,
    processed_at TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_ai_status ON article_ai_analysis(status);
CREATE INDEX IF NOT EXISTS idx_ai_category ON article_ai_analysis(main_category);
CREATE INDEX IF NOT EXISTS idx_ai_impact ON article_ai_analysis(industry_impact);
CREATE INDEX IF NOT EXISTS idx_ai_relevance ON article_ai_analysis(telecom_relevance);

CREATE TABLE IF NOT EXISTS daily_briefings (
    briefing_date TEXT PRIMARY KEY,
    period_label TEXT NOT NULL,
    status TEXT NOT NULL,                    -- COMPLETED | INSUFFICIENT_DATA | FAILED
    headline TEXT,
    intro_overview TEXT,
    developments_json TEXT,
    overall_implications TEXT,
    source_article_ids_json TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    model_name TEXT,
    prompt_version TEXT,
    generated_at TEXT NOT NULL,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS llm_job_locks (
    job_name TEXT PRIMARY KEY,
    pid INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    status TEXT NOT NULL
);
"""


class IntelligenceRepository:
    """SQLite persistence layer for AI analyses, daily briefings, and job concurrency locks."""

    def __init__(self, db_path: str | Path = "data/bd_telecom_news.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return open_sqlite_connection(self.db_path, timeout=30.0)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(NEWS_SCHEMA_SQL)
            conn.executescript(LLM_SCHEMA_SQL)

    # --- Concurrency Lock ---

    @staticmethod
    def _check_lock_owner_state(pid: int, started_at_iso: str) -> str:
        """Check whether a lock-holding PID is verified 'alive', 'dead', or 'unknown'.

        Guards against PID reuse by comparing the OS process creation time against
        the lock's `started_at` timestamp (with a small 10-second clock tolerance).
        """
        if pid <= 0:
            return "dead"
        if pid == os.getpid():
            return "alive"
        try:
            proc = psutil.Process(pid)
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                return "dead"
            try:
                lock_ts = datetime.fromisoformat(started_at_iso).timestamp()
                # If the current process with this PID started significantly AFTER the lock,
                # the original owner died and the OS recycled the PID.
                if proc.create_time() > lock_ts + 10.0:
                    return "dead"
                return "alive"
            except (ValueError, OSError):
                return "unknown"
        except psutil.NoSuchProcess:
            return "dead"
        except psutil.AccessDenied:
            # Process exists but belongs to another user; liveness cannot be verified.
            return "unknown"

    @staticmethod
    def _is_lock_owner_alive(pid: int, started_at_iso: str) -> bool:
        """Return True unless the lock-holding PID is verified dead or recycled."""
        return IntelligenceRepository._check_lock_owner_state(pid, started_at_iso) != "dead"

    def cleanup_dead_lock(self, job_name: str, stale_minutes: int = 30) -> bool:
        """Remove a lock if its owning PID is dead, or if liveness is unknown and it is expired.

        Never expires a lock whose owning PID and creation time are verified alive.
        Uses compare-and-swap predicates (`pid` and `started_at`) so concurrent lock
        replacements cannot be deleted accidentally.
        """
        now = datetime.now(DHAKA_TZ)
        stale_cutoff_dt = now - timedelta(minutes=stale_minutes)
        stale_cutoff_iso = stale_cutoff_dt.isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT pid, started_at FROM llm_job_locks WHERE job_name = ?",
                (job_name,),
            ).fetchone()
            if row is None:
                return False
            lock_pid = int(row["pid"])
            lock_started_at = str(row["started_at"])
            owner_state = self._check_lock_owner_state(lock_pid, lock_started_at)

            should_reclaim = False
            if owner_state == "dead":
                should_reclaim = True
            elif owner_state == "unknown":
                try:
                    is_expired = datetime.fromisoformat(lock_started_at) < stale_cutoff_dt
                except ValueError:
                    is_expired = lock_started_at < stale_cutoff_iso
                should_reclaim = is_expired

            if should_reclaim:
                cur = conn.execute(
                    "DELETE FROM llm_job_locks WHERE job_name = ? AND pid = ? AND started_at = ?",
                    (job_name, lock_pid, lock_started_at),
                )
                return cur.rowcount > 0
        return False

    def acquire_job_lock(self, job_name: str, stale_minutes: int = 30) -> bool:
        """Acquire a single-runner job lock; safely reclaims dead or indeterminate-stale locks."""
        self.cleanup_dead_lock(job_name, stale_minutes=stale_minutes)
        now = datetime.now(DHAKA_TZ)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO llm_job_locks (job_name, pid, started_at, status)
                VALUES (?, ?, ?, 'RUNNING')
                """,
                (job_name, os.getpid(), now.isoformat()),
            )
            return cur.rowcount > 0

    def release_job_lock(self, job_name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM llm_job_locks WHERE job_name = ? AND pid = ?",
                (job_name, os.getpid()),
            )

    def has_lock_row(self, job_name: str) -> bool:
        """Read-only check for whether a lock row currently exists (no writes)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM llm_job_locks WHERE job_name = ?",
                (job_name,),
            ).fetchone()
        return row is not None

    def is_job_running(self, job_name: str, stale_minutes: int = 30) -> bool:
        self.cleanup_dead_lock(job_name, stale_minutes=stale_minutes)
        return self.has_lock_row(job_name)

    # --- Eligible / Pending Articles ---

    def get_eligible_articles(
        self,
        *,
        limit: int = 10,
        max_retries: int = 2,
        article_ids: list[int] | None = None,
        force_reprocess: bool = False,
    ) -> list[dict[str, Any]]:
        """Return scraped articles that need AI processing (new, changed hash, or retryable failed)."""
        with self._connect() as conn:
            if article_ids:
                placeholders = ",".join("?" for _ in article_ids)
                if force_reprocess:
                    sql = f"""
                        SELECT a.* FROM articles a
                        WHERE a.id IN ({placeholders})
                        ORDER BY COALESCE(a.published_at, a.scraped_at) DESC
                    """
                    rows = conn.execute(sql, article_ids).fetchall()
                else:
                    sql = f"""
                        SELECT a.* FROM articles a
                        LEFT JOIN article_ai_analysis ai ON ai.article_id = a.id
                        WHERE a.id IN ({placeholders})
                          AND (
                              ai.article_id IS NULL
                              OR ai.status = 'PENDING'
                              OR ai.source_content_hash != a.content_hash
                              OR (ai.status = 'FAILED' AND ai.retry_count <= ?)
                          )
                        ORDER BY COALESCE(a.published_at, a.scraped_at) DESC
                    """
                    rows = conn.execute(sql, [*article_ids, max_retries]).fetchall()
            else:
                sql = """
                    SELECT a.* FROM articles a
                    LEFT JOIN article_ai_analysis ai ON ai.article_id = a.id
                    WHERE ai.article_id IS NULL
                       OR ai.status = 'PENDING'
                       OR ai.source_content_hash != a.content_hash
                       OR (ai.status = 'FAILED' AND ai.retry_count <= ?)
                    ORDER BY COALESCE(a.published_at, a.scraped_at) DESC
                    LIMIT ?
                """
                rows = conn.execute(sql, (max_retries, limit)).fetchall()
        return [dict(r) for r in rows]

    def mark_processing(self, article_id: int, content_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO article_ai_analysis (article_id, status, source_content_hash)
                VALUES (?, 'PROCESSING', ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    status = 'PROCESSING',
                    source_content_hash = excluded.source_content_hash,
                    error_message = NULL
                """,
                (article_id, content_hash),
            )

    def save_analysis(
        self,
        *,
        article_id: int,
        content_hash: str,
        intel: ArticleIntelligence,
        model_name: str,
        prompt_version: str,
        processing_ms: int,
    ) -> None:
        now_iso = datetime.now(DHAKA_TZ).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO article_ai_analysis (
                    article_id, status, english_headline, summary, key_takeaways_json,
                    main_category, subcategory, organizations_json, operators_json,
                    sentiment, sentiment_confidence, industry_impact, telecom_relevance,
                    reported_facts_json, industry_implications,
                    model_name, prompt_version, source_content_hash,
                    retry_count, processing_ms, processed_at, error_message
                ) VALUES (?, 'COMPLETED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL)
                ON CONFLICT(article_id) DO UPDATE SET
                    status = 'COMPLETED',
                    english_headline = excluded.english_headline,
                    summary = excluded.summary,
                    key_takeaways_json = excluded.key_takeaways_json,
                    main_category = excluded.main_category,
                    subcategory = excluded.subcategory,
                    organizations_json = excluded.organizations_json,
                    operators_json = excluded.operators_json,
                    sentiment = excluded.sentiment,
                    sentiment_confidence = excluded.sentiment_confidence,
                    industry_impact = excluded.industry_impact,
                    telecom_relevance = excluded.telecom_relevance,
                    reported_facts_json = excluded.reported_facts_json,
                    industry_implications = excluded.industry_implications,
                    model_name = excluded.model_name,
                    prompt_version = excluded.prompt_version,
                    source_content_hash = excluded.source_content_hash,
                    retry_count = 0,
                    processing_ms = excluded.processing_ms,
                    processed_at = excluded.processed_at,
                    error_message = NULL
                """,
                (
                    article_id,
                    intel.english_headline,
                    intel.summary,
                    json.dumps(intel.key_takeaways, ensure_ascii=False),
                    intel.main_category,
                    intel.subcategory,
                    json.dumps(intel.organizations, ensure_ascii=False),
                    json.dumps(intel.operators_mentioned, ensure_ascii=False),
                    intel.sentiment,
                    intel.sentiment_confidence,
                    intel.industry_impact,
                    intel.telecom_relevance,
                    json.dumps(intel.reported_facts, ensure_ascii=False),
                    intel.industry_implications,
                    model_name,
                    prompt_version,
                    content_hash,
                    processing_ms,
                    now_iso,
                ),
            )

    def mark_failed(
        self,
        *,
        article_id: int,
        content_hash: str,
        model_name: str,
        prompt_version: str,
        error_message: str,
    ) -> None:
        now_iso = datetime.now(DHAKA_TZ).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO article_ai_analysis (
                    article_id, status, model_name, prompt_version,
                    source_content_hash, retry_count, processed_at, error_message
                ) VALUES (?, 'FAILED', ?, ?, ?, 1, ?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    status = 'FAILED',
                    model_name = excluded.model_name,
                    prompt_version = excluded.prompt_version,
                    source_content_hash = excluded.source_content_hash,
                    retry_count = article_ai_analysis.retry_count + 1,
                    processed_at = excluded.processed_at,
                    error_message = excluded.error_message
                """,
                (
                    article_id,
                    model_name,
                    prompt_version,
                    content_hash,
                    now_iso,
                    error_message[:600],
                ),
            )

    def get_queue_metrics(self) -> dict[str, Any]:
        """Return counts of total, completed, pending, processing, failed, and last AI timestamp."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            completed = conn.execute(
                "SELECT COUNT(*) FROM article_ai_analysis WHERE status = 'COMPLETED'"
            ).fetchone()[0]
            processing = conn.execute(
                "SELECT COUNT(*) FROM article_ai_analysis WHERE status = 'PROCESSING'"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM article_ai_analysis WHERE status = 'FAILED'"
            ).fetchone()[0]
            pending = conn.execute(
                """
                SELECT COUNT(*) FROM articles a
                LEFT JOIN article_ai_analysis ai ON ai.article_id = a.id
                WHERE ai.article_id IS NULL
                   OR ai.status = 'PENDING'
                   OR ai.source_content_hash != a.content_hash
                """
            ).fetchone()[0]
            last_ai = conn.execute(
                "SELECT MAX(processed_at) FROM article_ai_analysis WHERE status = 'COMPLETED'"
            ).fetchone()[0]
            last_scrape = conn.execute(
                "SELECT MAX(finished_at) FROM scrape_runs"
            ).fetchone()[0]
            active_model = conn.execute(
                "SELECT model_name FROM article_ai_analysis WHERE status = 'COMPLETED' ORDER BY processed_at DESC LIMIT 1"
            ).fetchone()
        return {
            "total_articles": total,
            "completed": completed,
            "pending": pending,
            "processing": processing,
            "failed": failed,
            "last_ai_processed_at": last_ai,
            "last_scrape_at": last_scrape,
            "last_model_used": active_model[0] if active_model else None,
        }

    def get_recent_errors(self, limit: int = 15) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ai.article_id, a.publisher, a.headline, ai.retry_count,
                       ai.model_name, ai.processed_at, ai.error_message
                FROM article_ai_analysis ai
                JOIN articles a ON a.id = ai.article_id
                WHERE ai.status = 'FAILED'
                ORDER BY ai.processed_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Briefings ---

    def get_briefing(self, briefing_date: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM daily_briefings WHERE briefing_date = ?",
                (briefing_date,),
            ).fetchone()
        if row is None:
            return None
        return self._deserialize_briefing(row)

    def get_latest_briefing(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM daily_briefings ORDER BY briefing_date DESC, generated_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return self._deserialize_briefing(row)

    def save_briefing(
        self,
        *,
        briefing_date: str,
        period_label: str,
        status: str,
        output: DailyBriefingOutput | None,
        source_article_ids: list[int],
        source_fingerprint: str,
        model_name: str,
        prompt_version: str,
        error_message: str | None = None,
    ) -> None:
        now_iso = datetime.now(DHAKA_TZ).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_briefings (
                    briefing_date, period_label, status, headline, intro_overview,
                    developments_json, overall_implications,
                    source_article_ids_json, source_fingerprint,
                    model_name, prompt_version, generated_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(briefing_date) DO UPDATE SET
                    period_label = excluded.period_label,
                    status = excluded.status,
                    headline = excluded.headline,
                    intro_overview = excluded.intro_overview,
                    developments_json = excluded.developments_json,
                    overall_implications = excluded.overall_implications,
                    source_article_ids_json = excluded.source_article_ids_json,
                    source_fingerprint = excluded.source_fingerprint,
                    model_name = excluded.model_name,
                    prompt_version = excluded.prompt_version,
                    generated_at = excluded.generated_at,
                    error_message = excluded.error_message
                """,
                (
                    briefing_date,
                    period_label,
                    status,
                    output.headline if output else None,
                    output.intro_overview if output else None,
                    json.dumps([d.model_dump() for d in output.developments], ensure_ascii=False) if output else "[]",
                    output.overall_implications if output else None,
                    json.dumps(source_article_ids),
                    source_fingerprint,
                    model_name,
                    prompt_version,
                    now_iso,
                    error_message,
                ),
            )

    @staticmethod
    def _deserialize_briefing(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "briefing_date": row["briefing_date"],
            "period_label": row["period_label"],
            "status": row["status"],
            "headline": row["headline"],
            "intro_overview": row["intro_overview"],
            "developments": json.loads(row["developments_json"]) if row["developments_json"] else [],
            "overall_implications": row["overall_implications"],
            "source_article_ids": json.loads(row["source_article_ids_json"]),
            "source_fingerprint": row["source_fingerprint"],
            "model_name": row["model_name"],
            "prompt_version": row["prompt_version"],
            "generated_at": row["generated_at"],
            "error_message": row["error_message"],
        }
