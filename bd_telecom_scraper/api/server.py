"""Lightweight local Starlette + Uvicorn REST API for One-Click News Refresh.

Endpoints (loopback 127.0.0.1 only):
  - POST /api/news/refresh         -> Triggers asynchronous NewsOrchestrator pipeline
  - GET  /api/news/refresh/status  -> Live stage progress, metrics & last refresh timestamp
  - GET  /api/health               -> API health check

Includes loopback Origin/CSRF protection and strict Pydantic payload validation.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import uvicorn
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..orchestrator import NewsOrchestrator, RefreshOptions

API_DEFAULT_HOST = "127.0.0.1"
MAX_REFRESH_BODY_BYTES = 16 * 1024

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_FORBIDDEN_CSRF_CONTENT_TYPES = frozenset(
    {
        "application/x-www-form-urlencoded",
        "multipart/form-data",
        "text/plain",
    }
)
_server_boot_lock = threading.Lock()


def get_api_port(default: int = 8000) -> int:
    """Resolve `TELECOM_API_PORT` safely, falling back to `default` when unset or blank."""
    raw = os.getenv("TELECOM_API_PORT")
    if raw is None or not raw.strip():
        return default
    try:
        port = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid integer for environment variable TELECOM_API_PORT: {raw!r}") from exc
    if not (1 <= port <= 65535):
        raise ValueError(f"Environment variable TELECOM_API_PORT must be between 1 and 65535, got {port}")
    return port


API_DEFAULT_PORT = get_api_port()
API_DEFAULT_URL = f"http://{API_DEFAULT_HOST}:{API_DEFAULT_PORT}"


class RefreshRequest(BaseModel):
    """Strictly validated payload for POST /api/news/refresh."""

    model_config = ConfigDict(extra="forbid")

    days: StrictInt = Field(default=14, ge=1, le=90)
    max_per_source: StrictInt = Field(default=30, ge=1, le=100)
    llm_batch_limit: StrictInt = Field(default=25, ge=1, le=100)
    run_tavily: StrictBool = True
    run_llm: StrictBool = True
    run_briefing: StrictBool = True


def _is_loopback_origin(value: str) -> bool:
    raw = value.strip()
    if not raw or raw.lower() == "null":
        return False
    parsed = urlparse(raw)
    return parsed.scheme in ("http", "https") and (parsed.hostname or "").lower() in _LOOPBACK_HOSTS


def _extract_media_type(request: Request) -> str:
    return (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()


def _check_csrf_origin(request: Request) -> str | None:
    """Reject untrusted browser cross-origin requests to loopback state-changing endpoints."""
    sec_fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if sec_fetch_site == "cross-site":
        return "Cross-site request blocked."

    origin = request.headers.get("origin")
    if origin is not None and not _is_loopback_origin(origin):
        return f"Untrusted Origin header rejected: {origin[:80]}"

    referer = request.headers.get("referer")
    if referer is not None and not _is_loopback_origin(referer):
        return "Untrusted Referer header rejected."

    return None


def create_app(
    db_path: str | Path = "data/bd_telecom_news.db",
    *,
    orchestrator: NewsOrchestrator | None = None,
) -> Starlette:
    """Create the local Starlette ASGI app wired to `NewsOrchestrator`."""
    orch = orchestrator or NewsOrchestrator(db_path=db_path)

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "bangladesh-telecom-intel-api"})

    async def refresh_news(request: Request) -> JSONResponse:
        csrf_error = _check_csrf_origin(request)
        if csrf_error:
            return JSONResponse({"detail": csrf_error}, status_code=403)

        media_type = _extract_media_type(request)
        if media_type in _FORBIDDEN_CSRF_CONTENT_TYPES:
            return JSONResponse(
                {"detail": f"Unsupported Content-Type '{media_type}'. Use application/json."},
                status_code=415,
            )

        content_length_hdr = request.headers.get("content-length")
        if content_length_hdr is not None:
            try:
                declared_len = int(content_length_hdr.strip())
            except ValueError:
                return JSONResponse({"detail": "Invalid Content-Length header."}, status_code=400)
            if declared_len < 0:
                return JSONResponse({"detail": "Invalid Content-Length header."}, status_code=400)
            if declared_len > MAX_REFRESH_BODY_BYTES:
                return JSONResponse(
                    {"detail": f"Request body exceeds maximum size of {MAX_REFRESH_BODY_BYTES} bytes."},
                    status_code=413,
                )

        chunks: list[bytes] = []
        total_bytes = 0
        async for chunk in request.stream():
            total_bytes += len(chunk)
            if total_bytes > MAX_REFRESH_BODY_BYTES:
                return JSONResponse(
                    {"detail": f"Request body exceeds maximum size of {MAX_REFRESH_BODY_BYTES} bytes."},
                    status_code=413,
                )
            chunks.append(chunk)
        raw = b"".join(chunks)

        if raw.strip():
            if media_type != "application/json":
                return JSONResponse(
                    {"detail": "Non-empty request body requires Content-Type: application/json."},
                    status_code=415,
                )
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return JSONResponse({"detail": "Malformed JSON request body."}, status_code=400)
            if not isinstance(parsed, dict):
                return JSONResponse({"detail": "Request body must be a JSON object."}, status_code=400)
        else:
            parsed = {}

        try:
            validated = RefreshRequest.model_validate(parsed)
        except ValidationError as exc:
            return JSONResponse(
                {"detail": "Invalid refresh parameters.", "errors": exc.errors()},
                status_code=422,
            )

        opts = RefreshOptions(
            days=validated.days,
            max_per_source=validated.max_per_source,
            llm_batch_limit=validated.llm_batch_limit,
            run_tavily=validated.run_tavily,
            run_llm=validated.run_llm,
            run_briefing=validated.run_briefing,
        )
        started, snapshot = orch.start_refresh_async(opts)
        if not started:
            return JSONResponse(
                {"message": "A refresh job is already running.", "job": snapshot},
                status_code=409,
            )
        return JSONResponse(
            {"message": "News refresh started.", "job": snapshot},
            status_code=202,
        )

    async def refresh_status(request: Request) -> JSONResponse:
        job_id = request.query_params.get("job_id")
        return JSONResponse(orch.get_status(job_id=job_id))

    return Starlette(
        routes=[
            Route("/api/health", health, methods=["GET"]),
            Route("/api/news/refresh", refresh_news, methods=["POST"]),
            Route("/api/news/refresh/status", refresh_status, methods=["GET"]),
        ]
    )


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def ensure_api_server_running(
    db_path: str | Path = "data/bd_telecom_news.db",
    *,
    host: str = API_DEFAULT_HOST,
    port: int | None = None,
) -> str:
    """Start the local Starlette/Uvicorn API in a background daemon thread if not already bound."""
    resolved_port = port if port is not None else get_api_port()
    base_url = f"http://{host}:{resolved_port}"
    if _is_port_open(host, resolved_port):
        return base_url

    with _server_boot_lock:
        if _is_port_open(host, resolved_port):
            return base_url
        app = create_app(db_path=db_path)
        config = uvicorn.Config(app, host=host, port=resolved_port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True, name="telecom-intel-api")
        thread.start()
        for _ in range(25):
            if _is_port_open(host, resolved_port):
                break
            time.sleep(0.1)
    return base_url


def main() -> None:
    app = create_app()
    uvicorn.run(app, host=API_DEFAULT_HOST, port=get_api_port(), log_level="info")


if __name__ == "__main__":
    main()
