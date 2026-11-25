"""Unit & ASGI API tests for the centralized NewsOrchestrator and local Starlette REST API.

Uses `httpx.AsyncClient` with `httpx.ASGITransport` directly (avoiding deprecated
`starlette.testclient.TestClient`).
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from bd_telecom_scraper.api.server import create_app
from bd_telecom_scraper.orchestrator import NewsOrchestrator, RefreshOptions


def _asgi_request(
    app: Any,
    method: str,
    path: str,
    *,
    json_body: Any = None,
    content: bytes | str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def _run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            return await client.request(
                method,
                path,
                json=json_body,
                content=content,
                headers=headers,
            )

    return asyncio.run(_run())


def test_api_refresh_concurrency_lock_and_status_polling(tmp_path: Path) -> None:
    db_path = tmp_path / "orch_api.db"
    gate = threading.Event()

    def rss_stage(_opts: RefreshOptions) -> dict[str, object]:
        gate.wait(timeout=3.0)
        return {"discovered": 40, "extracted": 12, "deduplicated": 5, "saved": 7, "detail": "RSS ok"}

    def tavily_stage(_opts: RefreshOptions) -> dict[str, object]:
        return {
            "discovered": 10,
            "extracted": 6,
            "deduplicated": 2,
            "saved": 4,
            "credits_used": 2.0,
            "detail": "Tavily ok",
        }

    def llm_stage(_opts: RefreshOptions) -> dict[str, object]:
        return {"processed": 11, "detail": "Ollama ok"}

    def briefing_stage(_opts: RefreshOptions) -> dict[str, object]:
        return {"detail": "Daily Brief COMPLETED"}

    orch = NewsOrchestrator(
        db_path=db_path,
        rss_runner=rss_stage,
        tavily_runner=tavily_stage,
        llm_runner=llm_stage,
        briefing_runner=briefing_stage,
    )
    app = create_app(db_path=db_path, orchestrator=orch)

    assert _asgi_request(app, "GET", "/api/health").status_code == 200

    # Verify IDLE schema includes tavily_credits_used consistently
    idle = _asgi_request(app, "GET", "/api/news/refresh/status").json()
    assert idle["status"] == "IDLE"
    assert idle["metrics"]["tavily_credits_used"] == 0.0

    # 1. Start refresh -> 202 Accepted
    r1 = _asgi_request(
        app,
        "POST",
        "/api/news/refresh",
        json_body={"days": 14},
        headers={"X-Requested-By": "bd-telecom-dashboard"},
    )
    assert r1.status_code == 202
    assert "access-control-allow-origin" not in {k.lower() for k in r1.headers}
    job_id = r1.json()["job"]["job_id"]
    assert job_id.startswith("refresh-")

    # 2. Simultaneous second click -> 409 Conflict (no duplicate job)
    r2 = _asgi_request(app, "POST", "/api/news/refresh", json_body={"days": 14})
    assert r2.status_code == 409

    # Release worker and wait for completion
    gate.set()
    for _ in range(40):
        st = _asgi_request(app, "GET", "/api/news/refresh/status").json()
        if st["status"] == "COMPLETED":
            break
        time.sleep(0.05)

    final_state = _asgi_request(app, "GET", "/api/news/refresh/status").json()
    assert final_state["status"] == "COMPLETED"
    assert final_state["is_running"] is False
    assert final_state["last_successful_refresh_at"] is not None
    assert final_state["metrics"]["discovered"] == 50
    assert final_state["metrics"]["extracted"] == 18
    assert final_state["metrics"]["deduplicated"] == 7
    assert final_state["metrics"]["processed"] == 11
    assert final_state["metrics"]["saved"] == 11
    assert final_state["metrics"]["tavily_credits_used"] == 2.0
    assert all(s["status"] == "completed" for s in final_state["stages"])


def test_partial_failure_isolation_preserves_other_stages(tmp_path: Path) -> None:
    db_path = tmp_path / "orch_partial.db"

    def rss_ok(_opts: RefreshOptions) -> dict[str, object]:
        return {"discovered": 20, "extracted": 5, "deduplicated": 1, "saved": 4, "detail": "RSS ok"}

    def tavily_fail(_opts: RefreshOptions) -> dict[str, object]:
        raise RuntimeError("Simulated Tavily timeout")

    def llm_ok(_opts: RefreshOptions) -> dict[str, object]:
        return {"processed": 4, "detail": "Ollama ok"}

    def brief_ok(_opts: RefreshOptions) -> dict[str, object]:
        return {"detail": "Brief ok"}

    orch = NewsOrchestrator(
        db_path=db_path,
        rss_runner=rss_ok,
        tavily_runner=tavily_fail,
        llm_runner=llm_ok,
        briefing_runner=brief_ok,
    )
    started, _ = orch.start_refresh_async(RefreshOptions(stage_retries=0))
    assert started is True

    for _ in range(40):
        st = orch.get_status()
        if st["status"] != "RUNNING":
            break
        time.sleep(0.05)

    final_state = orch.get_status()
    assert final_state["status"] == "PARTIAL_FAILURE"
    assert final_state["metrics"]["saved"] == 4
    assert final_state["metrics"]["processed"] == 4
    by_id = {s["id"]: s for s in final_state["stages"]}
    assert by_id["rss_collection"]["status"] == "completed"
    assert by_id["tavily_discovery"]["status"] == "failed"
    assert by_id["llm_analysis"]["status"] == "completed"
    assert by_id["briefing_clustering"]["status"] == "completed"
