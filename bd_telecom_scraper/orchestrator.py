"""Centralized News Orchestrator coordinating all existing collection, LLM, and briefing modules.

Reuses:
  - `TelecomNewsScraper` (RSS, sitemaps, Quintype API, extraction, normalization, dedup, storage)
  - `TavilyDiscoveryPipeline` (Tavily Search + Extract fallback, dedup, credit guard, storage)
  - `ArticleIntelligencePipeline` (Local Ollama summarization, sentiment, topic & entity analysis)
  - `DailyBriefingEngine` (Bangla-English event clustering & executive Daily Brief)

Persists live per-stage progress to SQLite (`refresh_jobs`) so browser reloads never
interrupt an active background job, and prevents simultaneous duplicate runs.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable

from .discovery import DHAKA_TZ
from .llm.briefing import DailyBriefingEngine
from .llm.config import load_llm_config
from .llm.pipeline import ArticleIntelligencePipeline
from .llm.repository import IntelligenceRepository
from .pipeline import TelecomNewsScraper
from .storage import SCHEMA_SQL as NEWS_SCHEMA_SQL, open_sqlite_connection
from .tavily.config import load_tavily_config
from .tavily.pipeline import TavilyDiscoveryPipeline

REFRESH_JOBS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS refresh_jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,                -- RUNNING | COMPLETED | PARTIAL_FAILURE | FAILED
    current_stage TEXT,
    stages_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_refresh_jobs_started ON refresh_jobs(started_at DESC);
"""

STAGE_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("rss_collection", "1. RSS & Portal Collection (Extract, Normalize & Deduplicate)"),
    ("tavily_discovery", "2. Tavily Search & Extract Fallback"),
    ("llm_analysis", "3. Local Ollama Analysis (Summary, Sentiment, Topics & Entities)"),
    ("briefing_clustering", "4. Bangla–English Clustering & Daily Brief"),
)


def _setup_orchestrator_logger() -> logging.Logger:
    log = logging.getLogger("bd_telecom_scraper.orchestrator")
    if not log.handlers:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / "news_refresh.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(fh)
        log.setLevel(logging.INFO)
    return log


logger = _setup_orchestrator_logger()


@dataclass(frozen=True)
class RefreshOptions:
    """Configurable parameters for a one-click refresh run."""

    days: int = 14
    max_per_source: int = 30
    llm_batch_limit: int = 25
    run_tavily: bool = True
    run_llm: bool = True
    run_briefing: bool = True
    stage_retries: int = 1


class NewsOrchestrator:
    """Coordinates asynchronous end-to-end news refresh with single-runner locking."""

    def __init__(
        self,
        db_path: str | Path = "data/bd_telecom_news.db",
        *,
        rss_runner: Callable[[RefreshOptions], dict[str, Any]] | None = None,
        tavily_runner: Callable[[RefreshOptions], dict[str, Any]] | None = None,
        llm_runner: Callable[[RefreshOptions], dict[str, Any]] | None = None,
        briefing_runner: Callable[[RefreshOptions], dict[str, Any]] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._process_lock = threading.Lock()
        self._active_thread: threading.Thread | None = None
        self.repo = IntelligenceRepository(self.db_path)
        self._init_db()
        self._rss_runner = rss_runner or self._default_rss_stage
        self._tavily_runner = tavily_runner or self._default_tavily_stage
        self._llm_runner = llm_runner or self._default_llm_stage
        self._briefing_runner = briefing_runner or self._default_briefing_stage

    def _connect(self) -> sqlite3.Connection:
        return open_sqlite_connection(self.db_path, timeout=20.0)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(NEWS_SCHEMA_SQL)
            conn.executescript(REFRESH_JOBS_SCHEMA_SQL)

    def _reconcile_interrupted_jobs(self) -> None:
        """Mark any orphaned RUNNING refresh_jobs as FAILED when no live lock owner exists.

        Uses a read-only fast path so routine `GET /api/news/refresh/status` polling performs
        zero SQLite writes when no RUNNING job or lock row exists.
        """
        thread_alive = self._active_thread is not None and self._active_thread.is_alive()
        if thread_alive:
            return

        with self._connect() as conn:
            has_lock = (
                conn.execute(
                    "SELECT 1 FROM llm_job_locks WHERE job_name = 'news_refresh'"
                ).fetchone()
                is not None
            )
            running_rows = conn.execute(
                "SELECT job_id, stages_json FROM refresh_jobs WHERE status = 'RUNNING'"
            ).fetchall()

        if not has_lock and not running_rows:
            return

        if has_lock:
            reclaimed = self.repo.cleanup_dead_lock("news_refresh", stale_minutes=45)
            if not reclaimed and self.repo.has_lock_row("news_refresh"):
                # Another live OS process genuinely holds the lock
                return

        if not running_rows:
            return

        now_iso = datetime.now(DHAKA_TZ).isoformat()
        with self._connect() as conn:
            for r in running_rows:
                stages = json.loads(r["stages_json"])
                for st_obj in stages:
                    if st_obj.get("status") == "running":
                        st_obj["status"] = "failed"
                        st_obj["error"] = "Interrupted: worker process exited before completion."
                conn.execute(
                    """
                    UPDATE refresh_jobs
                    SET status = 'FAILED',
                        current_stage = NULL,
                        stages_json = ?,
                        finished_at = ?,
                        error_message = 'Interrupted: previous worker process terminated unexpectedly.'
                    WHERE job_id = ? AND status = 'RUNNING'
                    """,
                    (json.dumps(stages, ensure_ascii=False), now_iso, r["job_id"]),
                )
                logger.warning("Reconciled interrupted refresh job %s as FAILED.", r["job_id"])

    def start_refresh_async(self, options: RefreshOptions | None = None) -> tuple[bool, dict[str, Any]]:
        """Start a background refresh job. Returns (started, job_snapshot).

        If a job is already running, returns (False, active_job_snapshot) so the API
        can respond with HTTP 409 Conflict without starting a duplicate job.
        """
        opts = options or RefreshOptions()
        with self._process_lock:
            self._reconcile_interrupted_jobs()
            if self._active_thread is not None and self._active_thread.is_alive():
                latest = self.get_status()
                return False, latest
            if not self.repo.acquire_job_lock("news_refresh", stale_minutes=45):
                latest = self.get_status()
                return False, latest

            job_id = f"refresh-{datetime.now(DHAKA_TZ).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            now_iso = datetime.now(DHAKA_TZ).isoformat()
            stages = [
                {"id": sid, "label": label, "status": "pending", "detail": "", "error": None}
                for sid, label in STAGE_DEFINITIONS
            ]
            metrics = {
                "discovered": 0,
                "extracted": 0,
                "deduplicated": 0,
                "processed": 0,
                "saved": 0,
                "tavily_credits_used": 0.0,
            }
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO refresh_jobs (
                        job_id, status, current_stage, stages_json, metrics_json, started_at
                    ) VALUES (?, 'RUNNING', ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        STAGE_DEFINITIONS[0][0],
                        json.dumps(stages),
                        json.dumps(metrics),
                        now_iso,
                    ),
                )

            worker = threading.Thread(
                target=self._run_job,
                args=(job_id, opts, stages, metrics),
                daemon=True,
                name=f"news-refresh-{job_id}",
            )
            self._active_thread = worker
            worker.start()
            logger.info("Started async news refresh job %s", job_id)
            return True, self.get_status(job_id=job_id)

    def get_status(self, job_id: str | None = None) -> dict[str, Any]:
        """Return the latest (or specific) refresh job state + last successful refresh timestamp."""
        self._reconcile_interrupted_jobs()
        with self._connect() as conn:
            if job_id:
                row = conn.execute(
                    "SELECT * FROM refresh_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM refresh_jobs ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
            last_ok = conn.execute(
                """
                SELECT MAX(finished_at) FROM refresh_jobs
                WHERE status IN ('COMPLETED', 'PARTIAL_FAILURE')
                """
            ).fetchone()
            last_scrape = conn.execute("SELECT MAX(finished_at) FROM scrape_runs").fetchone()

        last_success_ts = (last_ok[0] if last_ok and last_ok[0] else None) or (
            last_scrape[0] if last_scrape and last_scrape[0] else None
        )

        if row is None:
            return {
                "is_running": False,
                "job_id": None,
                "status": "IDLE",
                "current_stage": None,
                "stages": [],
                "metrics": {
                    "discovered": 0,
                    "extracted": 0,
                    "deduplicated": 0,
                    "processed": 0,
                    "saved": 0,
                    "tavily_credits_used": 0.0,
                },
                "started_at": None,
                "finished_at": None,
                "error_message": None,
                "last_successful_refresh_at": last_success_ts,
            }

        is_running = row["status"] == "RUNNING" and (
            (self._active_thread is not None and self._active_thread.is_alive())
            or self.repo.is_job_running("news_refresh", stale_minutes=45)
        )
        return {
            "is_running": is_running,
            "job_id": row["job_id"],
            "status": row["status"],
            "current_stage": row["current_stage"],
            "stages": json.loads(row["stages_json"]),
            "metrics": json.loads(row["metrics_json"]),
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "error_message": row["error_message"],
            "last_successful_refresh_at": last_success_ts,
        }

    def _update_job(
        self,
        job_id: str,
        *,
        status: str,
        current_stage: str | None,
        stages: list[dict[str, Any]],
        metrics: dict[str, Any],
        finished_at: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE refresh_jobs
                SET status = ?, current_stage = ?, stages_json = ?, metrics_json = ?,
                    finished_at = ?, error_message = ?
                WHERE job_id = ?
                """,
                (
                    status,
                    current_stage,
                    json.dumps(stages, ensure_ascii=False),
                    json.dumps(metrics, ensure_ascii=False),
                    finished_at,
                    error_message,
                    job_id,
                ),
            )

    def _execute_stage_with_retry(
        self,
        fn: Callable[[RefreshOptions], dict[str, Any]],
        opts: RefreshOptions,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(opts.stage_retries + 1):
            try:
                return fn(opts)
            except Exception as exc:
                last_exc = exc
                if attempt < opts.stage_retries:
                    logger.warning("Stage attempt %d failed (%s); retrying once...", attempt + 1, exc)
                    time.sleep(1.0)
        assert last_exc is not None
        raise last_exc

    def _run_job(
        self,
        job_id: str,
        opts: RefreshOptions,
        stages: list[dict[str, Any]],
        metrics: dict[str, Any],
    ) -> None:
        stage_runners: list[tuple[str, bool, Callable[[RefreshOptions], dict[str, Any]]]] = [
            ("rss_collection", True, self._rss_runner),
            ("tavily_discovery", opts.run_tavily, self._tavily_runner),
            ("llm_analysis", opts.run_llm, self._llm_runner),
            ("briefing_clustering", opts.run_briefing, self._briefing_runner),
        ]
        failed_stages: list[str] = []
        succeeded_stages = 0

        try:
            for idx, (stage_id, enabled, runner) in enumerate(stage_runners):
                stage_obj = stages[idx]
                if not enabled:
                    stage_obj["status"] = "skipped"
                    stage_obj["detail"] = "Disabled for this run"
                    self._update_job(job_id, status="RUNNING", current_stage=stage_id, stages=stages, metrics=metrics)
                    continue

                stage_obj["status"] = "running"
                self._update_job(job_id, status="RUNNING", current_stage=stage_id, stages=stages, metrics=metrics)
                logger.info("[%s] Running stage: %s", job_id, stage_id)

                try:
                    out = self._execute_stage_with_retry(runner, opts)
                    stage_obj["status"] = "completed"
                    stage_obj["detail"] = str(out.get("detail") or "Completed")
                    metrics["discovered"] += int(out.get("discovered", 0))
                    metrics["extracted"] += int(out.get("extracted", 0))
                    metrics["deduplicated"] += int(out.get("deduplicated", 0))
                    metrics["processed"] += int(out.get("processed", 0))
                    metrics["saved"] += int(out.get("saved", 0))
                    metrics["tavily_credits_used"] = round(
                        float(metrics.get("tavily_credits_used", 0.0)) + float(out.get("credits_used", 0.0)),
                        2,
                    )
                    succeeded_stages += 1
                    logger.info("[%s] Stage %s completed: %s", job_id, stage_id, stage_obj["detail"])
                except Exception as exc:
                    stage_obj["status"] = "failed"
                    stage_obj["error"] = f"{type(exc).__name__}: {exc}"
                    failed_stages.append(f"{stage_id}: {exc}")
                    logger.exception("[%s] Stage %s failed (isolated): %s", job_id, stage_id, exc)

                self._update_job(job_id, status="RUNNING", current_stage=stage_id, stages=stages, metrics=metrics)

            final_status = "COMPLETED"
            err_summary: str | None = None
            if failed_stages:
                final_status = "PARTIAL_FAILURE" if succeeded_stages > 0 else "FAILED"
                err_summary = " | ".join(failed_stages)[:600]

            self._update_job(
                job_id,
                status=final_status,
                current_stage=None,
                stages=stages,
                metrics=metrics,
                finished_at=datetime.now(DHAKA_TZ).isoformat(),
                error_message=err_summary,
            )
            logger.info("[%s] Finished with status=%s metrics=%s", job_id, final_status, metrics)
        finally:
            self.repo.release_job_lock("news_refresh")

    # --- Default Stage Runners (100% Reusing Existing Modules) ---

    def _default_rss_stage(self, opts: RefreshOptions) -> dict[str, Any]:
        scraper = TelecomNewsScraper(
            db_path=self.db_path,
            lookback_days=opts.days,
            max_article_fetches_per_source=opts.max_per_source,
        )
        rep = scraper.run()
        # Refresh JSON & CSV exports automatically alongside SQLite
        scraper.store.export_json(self.db_path.parent / "bd_telecom_news.json")
        scraper.store.export_csv(self.db_path.parent / "bd_telecom_news.csv")
        return {
            "discovered": rep.total_discovered,
            "extracted": rep.total_relevant,
            "deduplicated": rep.total_duplicates,
            "saved": rep.total_saved,
            "detail": f"Discovered {rep.total_discovered}, saved {rep.total_saved} new ({rep.total_duplicates} duplicates skipped)",
        }

    def _default_tavily_stage(self, opts: RefreshOptions) -> dict[str, Any]:
        cfg = load_tavily_config()
        if not cfg.has_api_key:
            return {"detail": "Skipped (TAVILY_API_KEY not set in .env)"}
        pipeline = TavilyDiscoveryPipeline(db_path=self.db_path, config=cfg)
        rep = pipeline.run(days=opts.days)
        return {
            "discovered": rep.hits_returned,
            "extracted": rep.extracted_primary + rep.extracted_tavily_fallback,
            "deduplicated": rep.skipped_already_collected,
            "saved": rep.articles_saved,
            "credits_used": rep.credits_used_this_run,
            "detail": (
                f"{rep.queries_executed} queries ({rep.queries_skipped_cooldown} on cooldown), "
                f"saved {rep.articles_saved} new ({rep.credits_used_this_run} credits)"
            ),
        }

    def _default_llm_stage(self, opts: RefreshOptions) -> dict[str, Any]:
        cfg = load_llm_config(batch_size_override=opts.llm_batch_limit)
        pipeline = ArticleIntelligencePipeline(db_path=self.db_path, config=cfg)
        res = pipeline.process_batch(limit=opts.llm_batch_limit)
        if res.ollama_unavailable:
            raise RuntimeError("Local Ollama is unavailable at " + cfg.base_url)
        return {
            "processed": res.completed,
            "detail": f"Analyzed {res.completed} articles ({res.failed} failed) with {cfg.model_name}",
        }

    def _default_briefing_stage(self, opts: RefreshOptions) -> dict[str, Any]:
        cfg = load_llm_config()
        engine = DailyBriefingEngine(db_path=self.db_path, config=cfg)
        brief = engine.generate_briefing(force=False)
        status = brief.get("status", "UNKNOWN")
        if status == "FAILED":
            raise RuntimeError(str(brief.get("error_message") or "Daily briefing failed"))
        dev_count = len(brief.get("developments", []))
        return {
            "detail": f"Daily Brief {status} ({dev_count} consolidated developments)",
        }
