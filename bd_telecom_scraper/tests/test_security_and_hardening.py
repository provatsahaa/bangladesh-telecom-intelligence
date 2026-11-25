"""Hardening & security tests: credentials, dead-PID locks, retries, CSRF, validation, SQLite PRAGMAs."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psutil
import pytest

from bd_telecom_scraper.api.server import MAX_REFRESH_BODY_BYTES, create_app, get_api_port
from bd_telecom_scraper.dashboard.app import _api_trigger_refresh
from bd_telecom_scraper.discovery import DHAKA_TZ
from bd_telecom_scraper.facebook.graph_client import MetaGraphClient
from bd_telecom_scraper.llm.config import load_llm_config
from bd_telecom_scraper.llm.repository import IntelligenceRepository
from bd_telecom_scraper.models import Article
from bd_telecom_scraper.orchestrator import NewsOrchestrator, RefreshOptions
from bd_telecom_scraper.storage import ArticleStore, open_sqlite_connection
from bd_telecom_scraper.tavily.config import TavilyConfig, is_usable_credential, load_tavily_config


def _asgi_post(
    app: Any,
    path: str,
    *,
    json_body: Any = None,
    content: bytes | str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def _run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            return await client.post(path, json=json_body, content=content, headers=headers)

    return asyncio.run(_run())


def test_empty_and_placeholder_api_credentials_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for bad in ("", "   ", "your_tavily_api_key_here", "your_meta_graph_access_token_here", "CHANGEME", "<your_key>", "placeholder_123"):
        assert is_usable_credential(bad) is False
        assert TavilyConfig(api_key=bad).has_api_key is False
        assert MetaGraphClient(access_token=bad).has_token is False

    # Realistic keys remain accepted
    assert TavilyConfig(api_key="tvly-prod-abc123456").has_api_key is True
    assert MetaGraphClient(access_token="EAAGm0PX4ZCpsBA123").has_token is True

    # Orchestrator skips Tavily cleanly when .env has blank or placeholder key
    env_file = tmp_path / ".env"
    env_file.write_text("TAVILY_API_KEY=your_tavily_api_key_here\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    orch = NewsOrchestrator(db_path=tmp_path / "cred.db")
    out = orch._default_tavily_stage(RefreshOptions())
    assert "Skipped" in str(out.get("detail", ""))


def test_dead_pid_lock_recovery_and_interrupted_job_reconciliation(tmp_path: Path) -> None:
    db_path = tmp_path / "dead_lock.db"
    repo = IntelligenceRepository(db_path)
    orch = NewsOrchestrator(db_path=db_path)

    # Simulate a crashed process: insert a lock for a non-existent PID + an orphaned RUNNING job
    dead_pid = 9999999
    now_iso = datetime.now(DHAKA_TZ).isoformat()
    with open_sqlite_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO llm_job_locks (job_name, pid, started_at, status) VALUES ('news_refresh', ?, ?, 'RUNNING')",
            (dead_pid, now_iso),
        )
        conn.execute(
            """
            INSERT INTO refresh_jobs (job_id, status, current_stage, stages_json, metrics_json, started_at)
            VALUES ('refresh-orphaned', 'RUNNING', 'rss_collection', ?, ?, ?)
            """,
            (
                json.dumps([{"id": "rss_collection", "label": "RSS", "status": "running", "detail": "", "error": None}]),
                json.dumps({"discovered": 0, "extracted": 0, "deduplicated": 0, "processed": 0, "saved": 0, "tavily_credits_used": 0.0}),
                now_iso,
            ),
        )

    # Status check must reclaim dead-PID lock and mark orphaned job FAILED
    st = orch.get_status()
    assert st["job_id"] == "refresh-orphaned"
    assert st["status"] == "FAILED"
    assert st["is_running"] is False
    assert "Interrupted" in (st["error_message"] or "")
    assert repo.is_job_running("news_refresh") is False

    # Now acquiring the lock succeeds immediately
    assert repo.acquire_job_lock("news_refresh") is True
    repo.release_job_lock("news_refresh")


def test_active_process_lock_is_preserved(tmp_path: Path) -> None:
    db_path = tmp_path / "active_lock.db"
    repo = IntelligenceRepository(db_path)

    # Lock owned by current live PID must NOT be reclaimed
    assert repo.acquire_job_lock("news_refresh", stale_minutes=30) is True
    assert repo.cleanup_dead_lock("news_refresh", stale_minutes=30) is False
    assert repo.acquire_job_lock("news_refresh", stale_minutes=30) is False
    assert repo.is_job_running("news_refresh", stale_minutes=30) is True
    repo.release_job_lock("news_refresh")
    assert repo.is_job_running("news_refresh", stale_minutes=30) is False


def test_orchestrator_stage_retry_recovery(tmp_path: Path) -> None:
    db_path = tmp_path / "retry.db"
    attempts = {"rss": 0}

    def flaky_rss(_opts: RefreshOptions) -> dict[str, object]:
        attempts["rss"] += 1
        if attempts["rss"] == 1:
            raise RuntimeError("Transient network hiccup")
        return {"discovered": 15, "extracted": 3, "deduplicated": 1, "saved": 2, "detail": "Recovered on retry"}

    orch = NewsOrchestrator(
        db_path=db_path,
        rss_runner=flaky_rss,
        tavily_runner=lambda _o: {"detail": "ok"},
        llm_runner=lambda _o: {"processed": 2, "detail": "ok"},
        briefing_runner=lambda _o: {"detail": "ok"},
    )
    started, _ = orch.start_refresh_async(RefreshOptions(stage_retries=1))
    assert started is True

    for _ in range(50):
        st = orch.get_status()
        if st["status"] != "RUNNING":
            break
        time.sleep(0.05)

    final = orch.get_status()
    assert attempts["rss"] == 2
    assert final["status"] == "COMPLETED"
    assert final["metrics"]["saved"] == 2


def test_api_csrf_protection_and_payload_validation(tmp_path: Path) -> None:
    db_path = tmp_path / "api_sec.db"
    orch = NewsOrchestrator(
        db_path=db_path,
        rss_runner=lambda _o: {"saved": 0, "detail": "ok"},
        tavily_runner=lambda _o: {"detail": "ok"},
        llm_runner=lambda _o: {"detail": "ok"},
        briefing_runner=lambda _o: {"detail": "ok"},
    )
    app = create_app(db_path=db_path, orchestrator=orch)

    # 1. Cross-origin CSRF attempts -> 403 Forbidden
    r_evil = _asgi_post(app, "/api/news/refresh", json_body={}, headers={"Origin": "https://evil.example"})
    assert r_evil.status_code == 403

    r_null = _asgi_post(app, "/api/news/refresh", json_body={}, headers={"Origin": "null"})
    assert r_null.status_code == 403

    r_secfetch = _asgi_post(app, "/api/news/refresh", json_body={}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r_secfetch.status_code == 403

    r_ref = _asgi_post(app, "/api/news/refresh", json_body={}, headers={"Referer": "https://evil.example/attack"})
    assert r_ref.status_code == 403

    # 2. Malformed & non-object JSON -> 400 Bad Request
    r_bad_json = _asgi_post(app, "/api/news/refresh", content="{not valid json", headers={"Content-Type": "application/json"})
    assert r_bad_json.status_code == 400

    r_list = _asgi_post(app, "/api/news/refresh", content="[1, 2, 3]", headers={"Content-Type": "application/json"})
    assert r_list.status_code == 400

    # 3. Out-of-range / wrong-type / unknown fields -> 422 Unprocessable Entity
    assert _asgi_post(app, "/api/news/refresh", json_body={"days": 0}).status_code == 422
    assert _asgi_post(app, "/api/news/refresh", json_body={"days": 365}).status_code == 422
    assert _asgi_post(app, "/api/news/refresh", json_body={"llm_batch_limit": "ten"}).status_code == 422
    assert _asgi_post(app, "/api/news/refresh", json_body={"unknown_param": True}).status_code == 422

    # 4. Legitimate loopback Origin request -> 202 Accepted (and no permissive CORS header)
    r_ok = _asgi_post(
        app,
        "/api/news/refresh",
        json_body={"days": 7, "llm_batch_limit": 5},
        headers={"Origin": "http://127.0.0.1:8501", "X-Requested-By": "bd-telecom-dashboard"},
    )
    assert r_ok.status_code == 202
    assert "access-control-allow-origin" not in {k.lower() for k in r_ok.headers}

    # Wait briefly for worker thread to finish before tmp_path cleanup
    for _ in range(30):
        if orch.get_status()["status"] != "RUNNING":
            break
        time.sleep(0.05)


def test_sqlite_foreign_keys_and_wal_mode_enforced(tmp_path: Path) -> None:
    db_path = tmp_path / "fk_wal.db"
    store = ArticleStore(db_path)
    repo = IntelligenceRepository(db_path)

    with open_sqlite_connection(db_path) as conn:
        fk_enabled = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        journal_mode = str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower()
        assert fk_enabled == 1
        assert journal_mode == "wal"

        # Inserting an AI analysis row for a non-existent article_id MUST fail with IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO article_ai_analysis (article_id, status, source_content_hash) VALUES (99999, 'PENDING', 'h')"
            )

    # Valid article + AI analysis row should cascade delete cleanly when article is deleted
    store.save_article(
        Article(
            url="https://www.tbsnews.net/tech/fk-test",
            canonical_url="https://www.tbsnews.net/tech/fk-test",
            headline="BTRC FK test article",
            publisher="The Business Standard",
            source_id="tbsnews",
            language="en",
            published_at=datetime(2026, 11, 25, 10, 0, tzinfo=DHAKA_TZ),
            scraped_at=datetime(2026, 11, 25, 11, 0, tzinfo=DHAKA_TZ),
            summary="Summary",
            content="Content body text",
            entities=["BTRC"],
            matched_topics=["regulation_btrc"],
            matched_keywords=["btrc"],
            relevance_score=4.0,
            discovery_method="rss",
            content_hash="fk_hash",
            title_fingerprint="fk_fp",
        )
    )
    repo.mark_processing(1, "fk_hash")
    with open_sqlite_connection(db_path) as conn:
        conn.execute("DELETE FROM articles WHERE id = 1")
        remaining = conn.execute("SELECT COUNT(*) FROM article_ai_analysis WHERE article_id = 1").fetchone()[0]
        assert remaining == 0


def test_api_content_type_size_limit_strict_types_and_dashboard_trigger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "api_strict.db"
    gate = threading.Event()

    def slow_rss(_opts: RefreshOptions) -> dict[str, object]:
        gate.wait(timeout=3.0)
        return {"saved": 0, "detail": "ok"}

    orch = NewsOrchestrator(
        db_path=db_path,
        rss_runner=slow_rss,
        tavily_runner=lambda _o: {"detail": "ok"},
        llm_runner=lambda _o: {"detail": "ok"},
        briefing_runner=lambda _o: {"detail": "ok"},
    )
    app = create_app(db_path=db_path, orchestrator=orch)

    # 1. Form and text/plain Content-Types rejected with 415 (blocks simple-request CSRF)
    for forbidden_ct in (
        "application/x-www-form-urlencoded",
        "application/x-www-form-urlencoded; charset=utf-8",
        "multipart/form-data; boundary=----abc",
        "text/plain",
        "text/plain; charset=utf-8",
    ):
        resp = _asgi_post(app, "/api/news/refresh", content="days=7", headers={"Content-Type": forbidden_ct})
        assert resp.status_code == 415, f"Expected 415 for {forbidden_ct}, got {resp.status_code}"
        resp_empty = _asgi_post(app, "/api/news/refresh", content=b"", headers={"Content-Type": forbidden_ct})
        assert resp_empty.status_code == 415

    # 2. Non-empty body without application/json rejected with 415
    r_no_ct = _asgi_post(app, "/api/news/refresh", content=b'{"days": 7}')
    assert r_no_ct.status_code == 415
    r_wrong_ct = _asgi_post(
        app,
        "/api/news/refresh",
        content=b'{"days": 7}',
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r_wrong_ct.status_code == 415

    # 3. Foreign Referer rejected even alongside loopback Origin
    r_bad_ref = _asgi_post(
        app,
        "/api/news/refresh",
        json_body={},
        headers={"Origin": "http://127.0.0.1:8501", "Referer": "https://evil.example/page"},
    )
    assert r_bad_ref.status_code == 403

    # 4. Payload exceeding 16 KB rejected with 413 Payload Too Large (both Content-Length and streamed)
    oversized = b'{"days": 7, "pad": "' + (b"x" * (MAX_REFRESH_BODY_BYTES + 64)) + b'"}'
    r_too_large = _asgi_post(
        app,
        "/api/news/refresh",
        content=oversized,
        headers={"Content-Type": "application/json"},
    )
    assert r_too_large.status_code == 413

    async def _chunked_oversized() -> httpx.Response:
        async def _gen():
            for _ in range(5):
                yield b"a" * 4096

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            return await client.post(
                "/api/news/refresh",
                content=_gen(),
                headers={"Content-Type": "application/json"},
            )

    r_stream_large = asyncio.run(_chunked_oversized())
    assert r_stream_large.status_code == 413

    # 5. Strict Pydantic validation rejects booleans/strings/floats where ints/bools are required
    for bad_payload in (
        {"days": True},
        {"days": False},
        {"days": "14"},
        {"days": 14.0},
        {"max_per_source": "30"},
        {"llm_batch_limit": True},
        {"run_llm": "true"},
        {"run_tavily": 1},
    ):
        resp = _asgi_post(app, "/api/news/refresh", json_body=bad_payload)
        assert resp.status_code == 422, f"Expected 422 for {bad_payload}, got {resp.status_code}"

    # 6. Verify actual dashboard `_api_trigger_refresh()` request succeeds (202 -> then 409 while running)
    def _mock_httpx_post(url: str, *, headers: dict[str, str] | None = None, json: Any = None, timeout: float = 6.0):
        assert url == "http://127.0.0.1:8000/api/news/refresh"
        return _asgi_post(app, "/api/news/refresh", json_body=json, headers=headers)

    monkeypatch.setattr(httpx, "post", _mock_httpx_post)
    ok1, msg1, job1 = _api_trigger_refresh("http://127.0.0.1:8000")
    assert ok1 is True
    assert "started" in msg1.lower()
    assert job1["job_id"].startswith("refresh-")

    ok2, msg2, _job2 = _api_trigger_refresh("http://127.0.0.1:8000")
    assert ok2 is False
    assert "already running" in msg2.lower()

    gate.set()
    for _ in range(40):
        if orch.get_status()["status"] != "RUNNING":
            break
        time.sleep(0.05)

    # 7. Empty-body loopback CLI POST (no Content-Type) still works
    gate.set()
    r_empty_cli = _asgi_post(app, "/api/news/refresh")
    assert r_empty_cli.status_code == 202
    for _ in range(40):
        if orch.get_status()["status"] != "RUNNING":
            break
        time.sleep(0.05)


class _FakeProcess:
    def __init__(self, *, running: bool = True, status: str = psutil.STATUS_RUNNING, create_ts: float = 0.0) -> None:
        self._running = running
        self._status = status
        self._create_ts = create_ts

    def is_running(self) -> bool:
        return self._running

    def status(self) -> str:
        return self._status

    def create_time(self) -> float:
        return self._create_ts


def test_lock_recovery_pid_reuse_live_long_job_and_access_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "lock_states.db"
    repo = IntelligenceRepository(db_path)

    # Case A: PID reuse (OS recycled PID after lock started) -> MUST reclaim
    lock_dt = datetime.now(DHAKA_TZ) - timedelta(minutes=5)
    recycled_pid = 424242
    with open_sqlite_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO llm_job_locks (job_name, pid, started_at, status) VALUES ('news_refresh', ?, ?, 'RUNNING')",
            (recycled_pid, lock_dt.isoformat()),
        )
    monkeypatch.setattr(
        psutil,
        "Process",
        lambda pid: _FakeProcess(running=True, create_ts=lock_dt.timestamp() + 30.0),
    )
    assert repo.cleanup_dead_lock("news_refresh", stale_minutes=45) is True
    assert repo.has_lock_row("news_refresh") is False

    # Case B: Verified-live long-running job (> 45 minutes old) -> MUST NOT expire
    old_live_dt = datetime.now(DHAKA_TZ) - timedelta(minutes=90)
    live_pid = 525252
    with open_sqlite_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO llm_job_locks (job_name, pid, started_at, status) VALUES ('news_refresh', ?, ?, 'RUNNING')",
            (live_pid, old_live_dt.isoformat()),
        )
    monkeypatch.setattr(
        psutil,
        "Process",
        lambda pid: _FakeProcess(running=True, create_ts=old_live_dt.timestamp() - 5.0),
    )
    assert repo.cleanup_dead_lock("news_refresh", stale_minutes=45) is False
    assert repo.has_lock_row("news_refresh") is True

    # Case C: Indeterminate liveness (AccessDenied) -> preserved while fresh, reclaimed once stale
    with open_sqlite_connection(db_path) as conn:
        conn.execute("DELETE FROM llm_job_locks WHERE job_name = 'news_refresh'")
        conn.execute(
            "INSERT INTO llm_job_locks (job_name, pid, started_at, status) VALUES ('news_refresh', ?, ?, 'RUNNING')",
            (626262, (datetime.now(DHAKA_TZ) - timedelta(minutes=10)).isoformat()),
        )

    def _raise_access_denied(_pid: int) -> Any:
        raise psutil.AccessDenied(pid=_pid)

    monkeypatch.setattr(psutil, "Process", _raise_access_denied)
    assert repo.cleanup_dead_lock("news_refresh", stale_minutes=45) is False
    assert repo.has_lock_row("news_refresh") is True

    with open_sqlite_connection(db_path) as conn:
        conn.execute(
            "UPDATE llm_job_locks SET started_at = ? WHERE job_name = 'news_refresh'",
            ((datetime.now(DHAKA_TZ) - timedelta(minutes=60)).isoformat(),),
        )
    assert repo.cleanup_dead_lock("news_refresh", stale_minutes=45) is True
    assert repo.has_lock_row("news_refresh") is False


def test_lock_cleanup_cas_protects_replaced_lock_and_handles_concurrency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "lock_cas.db"
    repo = IntelligenceRepository(db_path)

    # 1. Lock replacement between SELECT and DELETE -> CAS predicate MUST protect the new lock
    old_ts = (datetime.now(DHAKA_TZ) - timedelta(minutes=20)).isoformat()
    new_ts = datetime.now(DHAKA_TZ).isoformat()
    dead_pid = 9999991
    with open_sqlite_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO llm_job_locks (job_name, pid, started_at, status) VALUES ('news_refresh', ?, ?, 'RUNNING')",
            (dead_pid, old_ts),
        )

    def _replace_during_check(pid: int, started_at_iso: str) -> str:
        assert pid == dead_pid
        # Simulate another worker reclaiming and acquiring a brand-new live lock concurrently
        with open_sqlite_connection(db_path) as inner_conn:
            inner_conn.execute(
                "UPDATE llm_job_locks SET pid = ?, started_at = ? WHERE job_name = 'news_refresh'",
                (os.getpid(), new_ts),
            )
        return "dead"

    monkeypatch.setattr(repo, "_check_lock_owner_state", _replace_during_check)
    assert repo.cleanup_dead_lock("news_refresh", stale_minutes=45) is False
    with open_sqlite_connection(db_path) as conn:
        row = conn.execute(
            "SELECT pid, started_at FROM llm_job_locks WHERE job_name = 'news_refresh'"
        ).fetchone()
        assert row is not None
        assert int(row["pid"]) == os.getpid()
        assert str(row["started_at"]) == new_ts

    # 2. Two concurrent cleanup attempts on a dead lock -> exactly one wins
    monkeypatch.undo()
    with open_sqlite_connection(db_path) as conn:
        conn.execute("DELETE FROM llm_job_locks WHERE job_name = 'news_refresh'")
        conn.execute(
            "INSERT INTO llm_job_locks (job_name, pid, started_at, status) VALUES ('news_refresh', ?, ?, 'RUNNING')",
            (9999992, old_ts),
        )

    barrier = threading.Barrier(2)
    results: list[bool] = []
    results_lock = threading.Lock()

    def _worker() -> None:
        barrier.wait()
        won = repo.cleanup_dead_lock("news_refresh", stale_minutes=45)
        with results_lock:
            results.append(won)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)
    assert sorted(results) == [False, True]
    assert repo.has_lock_row("news_refresh") is False


def test_status_poll_performs_zero_sqlite_writes_when_idle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "readonly_poll.db"
    orch = NewsOrchestrator(db_path=db_path)

    writes: list[tuple[int, str | None]] = []
    orig_connect = orch._connect

    def _audited_connect() -> sqlite3.Connection:
        conn = orig_connect()

        def _authorizer(action: int, arg1: str | None, _arg2: str | None, _db: str | None, _trigger: str | None) -> int:
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                writes.append((action, arg1))
            return sqlite3.SQLITE_OK

        conn.set_authorizer(_authorizer)
        return conn

    monkeypatch.setattr(orch, "_connect", _audited_connect)
    monkeypatch.setattr(orch.repo, "_connect", _audited_connect)

    st = orch.get_status()
    assert st["status"] == "IDLE"
    assert writes == []


def test_empty_and_invalid_numeric_env_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Empty numeric env vars fall back cleanly to defaults
    for key in (
        "TELECOM_API_PORT",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "LLM_TEMPERATURE",
        "LLM_NUM_CTX",
        "LLM_MAX_INPUT_CHARS",
        "LLM_TIMEOUT_SECONDS",
        "LLM_BATCH_SIZE",
        "LLM_MAX_RETRIES",
        "TAVILY_BASE_URL",
        "TAVILY_SEARCH_DEPTH",
        "TAVILY_EXTRACT_DEPTH",
        "TAVILY_MAX_RESULTS",
        "TAVILY_MIN_INTERVAL_HOURS",
        "TAVILY_MONTHLY_CREDIT_BUDGET",
        "TAVILY_TIMEOUT_SECONDS",
    ):
        monkeypatch.setenv(key, "")

    assert get_api_port() == 8000

    llm_cfg = load_llm_config()
    assert llm_cfg.base_url == "http://localhost:11434"
    assert llm_cfg.model_name == "qwen2.5:3b"
    assert llm_cfg.temperature == 0.1
    assert llm_cfg.num_ctx == 4096
    assert llm_cfg.max_input_chars == 6000
    assert llm_cfg.timeout_seconds == 120.0
    assert llm_cfg.batch_size == 10
    assert llm_cfg.max_retries == 2

    tavily_cfg = load_tavily_config(env_path=tmp_path / "nonexistent.env")
    assert tavily_cfg.base_url == "https://api.tavily.com"
    assert tavily_cfg.search_depth == "basic"
    assert tavily_cfg.extract_depth == "basic"
    assert tavily_cfg.max_results_per_query == 10
    assert tavily_cfg.min_interval_hours == 12.0
    assert tavily_cfg.monthly_credit_budget == 800
    assert tavily_cfg.timeout_seconds == 25.0

    # Verify importing api.server directly in a fresh subprocess with TELECOM_API_PORT="" succeeds
    env_sub = os.environ.copy()
    env_sub["TELECOM_API_PORT"] = ""
    proc = subprocess.run(
        [sys.executable, "-c", "from bd_telecom_scraper.api.server import API_DEFAULT_PORT; assert API_DEFAULT_PORT == 8000"],
        env=env_sub,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    # 2. Invalid non-empty numeric values raise clear ValueError naming the variable
    monkeypatch.setenv("TELECOM_API_PORT", "abc")
    with pytest.raises(ValueError, match="TELECOM_API_PORT"):
        get_api_port()

    monkeypatch.setenv("TELECOM_API_PORT", "99999")
    with pytest.raises(ValueError, match="TELECOM_API_PORT"):
        get_api_port()

    monkeypatch.setenv("LLM_BATCH_SIZE", "not-a-number")
    with pytest.raises(ValueError, match="LLM_BATCH_SIZE"):
        load_llm_config()
    monkeypatch.setenv("LLM_BATCH_SIZE", "")

    monkeypatch.setenv("LLM_TEMPERATURE", "hot")
    with pytest.raises(ValueError, match="LLM_TEMPERATURE"):
        load_llm_config()
    monkeypatch.setenv("LLM_TEMPERATURE", "")

    monkeypatch.setenv("TAVILY_MAX_RESULTS", "many")
    with pytest.raises(ValueError, match="TAVILY_MAX_RESULTS"):
        load_tavily_config(env_path=tmp_path / "nonexistent.env")


def test_launcher_safe_dotenv_precedence_and_missing_binary_diagnostics(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_script = repo_root / "scripts" / "start_telecom_intel.sh"
    assert src_script.exists()

    # Copy launcher into an isolated temporary workspace
    sandbox_root = tmp_path / "proj"
    scripts_dir = sandbox_root / "scripts"
    scripts_dir.mkdir(parents=True)
    launcher = scripts_dir / "start_telecom_intel.sh"
    launcher.write_text(src_script.read_text(encoding="utf-8"), encoding="utf-8")
    launcher.chmod(0o755)

    pwn_marker = tmp_path / "pwned_marker"
    (sandbox_root / ".env").write_text(
        "\n".join(
            [
                "# Comment line",
                "TELECOM_API_PORT=8000",
                "STREAMLIT_SERVER_PORT=8501",
                "TAVILY_API_KEY=",
                f"INJECT_SUB=$(touch {pwn_marker})",
                f"INJECT_BT=`touch {pwn_marker}`",
                "SPECIAL_TOKEN='abc$HOME;echo hi|x=1'",
                'DOUBLE_QUOTED="val=123&456"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # 1. Missing .venv and missing streamlit on PATH -> clear diagnostic error, non-zero exit
    fake_bin_no_st = tmp_path / "bin_no_streamlit"
    fake_bin_no_st.mkdir()
    py_stub = fake_bin_no_st / "python3"
    py_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    py_stub.chmod(0o755)

    res_missing = subprocess.run(
        ["/usr/bin/env", "bash", str(launcher)],
        env={"PATH": f"{fake_bin_no_st}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_missing.returncode != 0
    assert "Streamlit executable not found" in res_missing.stderr
    assert not pwn_marker.exists()

    # 2. Custom caller ports override .env, special tokens are preserved literally, no shell execution
    fake_bin = tmp_path / "bin_ok"
    fake_bin.mkdir()
    dump_file = tmp_path / "captured_env.txt"

    # Stub python3 handles both API server launch and /api/health check cleanly
    (fake_bin / "python3").write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then exit 0; fi\n"
        "sleep 2\n",
        encoding="utf-8",
    )
    (fake_bin / "python3").chmod(0o755)

    # Stub streamlit records exported env vars and arguments then exits cleanly
    (fake_bin / "streamlit").write_text(
        "#!/bin/sh\n"
        f"printf 'ARGS=%s\\nAPI_PORT=%s\\nUI_PORT=%s\\nSPECIAL=%s\\nDOUBLE=%s\\nINJECT_SUB=%s\\nTAVILY=%s\\n' "
        "\"$*\" \"$TELECOM_API_PORT\" \"$STREAMLIT_SERVER_PORT\" \"$SPECIAL_TOKEN\" \"$DOUBLE_QUOTED\" \"$INJECT_SUB\" \"$TAVILY_API_KEY\" "
        f"> '{dump_file}'\n",
        encoding="utf-8",
    )
    (fake_bin / "streamlit").chmod(0o755)

    res_ok = subprocess.run(
        ["/usr/bin/env", "bash", str(launcher)],
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "TELECOM_API_PORT": "8095",
            "STREAMLIT_SERVER_PORT": "8595",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_ok.returncode == 0, f"stdout={res_ok.stdout}\nstderr={res_ok.stderr}"
    assert not pwn_marker.exists(), "Shell command inside .env MUST NOT execute!"
    captured = dump_file.read_text(encoding="utf-8")
    assert "API_PORT=8095" in captured
    assert "UI_PORT=8595" in captured
    assert "--server.port 8595" in captured
    assert "SPECIAL=abc$HOME;echo hi|x=1" in captured
    assert "DOUBLE=val=123&456" in captured
    assert f"INJECT_SUB=$(touch {pwn_marker})" in captured
    assert "TAVILY=\n" in captured


def test_launcher_port_validation_and_python_injection_prevention(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_script = repo_root / "scripts" / "start_telecom_intel.sh"
    script_text = src_script.read_text(encoding="utf-8")

    # Static guard: health-check command must read TELECOM_API_PORT via os.environ, never bash interpolation
    assert 'os.environ["TELECOM_API_PORT"]' in script_text
    assert "urlopen('http://127.0.0.1:${API_PORT}" not in script_text

    sandbox_root = tmp_path / "proj_port_sec"
    scripts_dir = sandbox_root / "scripts"
    scripts_dir.mkdir(parents=True)
    launcher = scripts_dir / "start_telecom_intel.sh"
    launcher.write_text(script_text, encoding="utf-8")
    launcher.chmod(0o755)

    fake_bin = tmp_path / "bin_real_py_c"
    fake_bin.mkdir()
    st_ran_marker = tmp_path / "streamlit_ran"
    py_inject_marker = tmp_path / "py_injected_marker"

    # Delegate `python3 -c` to the real Python interpreter so any code injection would actually execute
    (fake_bin / "python3").write_text(
        "#!/bin/sh\n"
        f"if [ \"$1\" = \"-c\" ]; then exec '{sys.executable}' \"$@\"; fi\n"
        "sleep 2\n",
        encoding="utf-8",
    )
    (fake_bin / "python3").chmod(0o755)
    (fake_bin / "streamlit").write_text(
        f"#!/bin/sh\ntouch '{st_ran_marker}'\nexit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "streamlit").chmod(0o755)

    base_env = {"PATH": f"{fake_bin}:/usr/bin:/bin"}

    # 1. Crafted Python `-c` injection payload inside .env MUST fail validation before launching anything
    inject_payload = f"8000') if False else open('{py_inject_marker}', 'w').write('pwn') #"
    (sandbox_root / ".env").write_text(
        f"TELECOM_API_PORT={inject_payload}\n",
        encoding="utf-8",
    )
    res_inj_env = subprocess.run(
        ["/usr/bin/env", "bash", str(launcher)],
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_inj_env.returncode != 0
    assert "TELECOM_API_PORT" in res_inj_env.stderr
    assert not py_inject_marker.exists(), "Python injection payload MUST NOT execute!"
    assert not st_ran_marker.exists()

    (sandbox_root / ".env").unlink()

    # 2. Invalid & out-of-range ports via caller env for both TELECOM_API_PORT and STREAMLIT_SERVER_PORT
    invalid_cases = [
        ("TELECOM_API_PORT", "abc"),
        ("TELECOM_API_PORT", "-8000"),
        ("TELECOM_API_PORT", "0"),
        ("TELECOM_API_PORT", "65536"),
        ("TELECOM_API_PORT", inject_payload),
        ("STREAMLIT_SERVER_PORT", "not_a_port"),
        ("STREAMLIT_SERVER_PORT", "0"),
        ("STREAMLIT_SERVER_PORT", "70000"),
    ]
    for var_name, bad_val in invalid_cases:
        env_case = dict(base_env)
        env_case[var_name] = bad_val
        res_bad = subprocess.run(
            ["/usr/bin/env", "bash", str(launcher)],
            env=env_case,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res_bad.returncode != 0, f"Expected failure for {var_name}={bad_val!r}"
        assert var_name in res_bad.stderr
        assert not py_inject_marker.exists()
        assert not st_ran_marker.exists()

    # 3. Early API server exit MUST be detected immediately via `kill -0 "$API_PID"` without starting Streamlit
    (fake_bin / "python3").write_text(
        "#!/bin/sh\n"
        "exit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "python3").chmod(0o755)

    t0 = time.monotonic()
    res_dead_api = subprocess.run(
        ["/usr/bin/env", "bash", str(launcher)],
        env={**base_env, "TELECOM_API_PORT": "8088", "STREAMLIT_SERVER_PORT": "8588"},
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.monotonic() - t0
    assert res_dead_api.returncode != 0
    assert "exited unexpectedly" in res_dead_api.stderr
    assert elapsed < 3.0, f"Expected immediate fail-fast on dead API PID, took {elapsed:.2f}s"
    assert not st_ran_marker.exists()
