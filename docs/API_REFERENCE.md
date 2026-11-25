# Local REST API Reference

The local Starlette + Uvicorn API (`bd_telecom_scraper/api/server.py`) binds strictly to loopback **`http://127.0.0.1:8000`** (configurable via `TELECOM_API_PORT`).

> **Security Note:** This API is designed for local single-user execution and has no built-in authentication. Do not bind it to `0.0.0.0` or expose it to public networks without an authenticated reverse proxy.

---

## Endpoints Summary

| Method | Path | Purpose | Status Codes |
| :---: | :--- | :--- | :---: |
| `GET` | `/api/health` | Service liveness check | `200` |
| `POST` | `/api/news/refresh` | Start asynchronous end-to-end refresh pipeline (strict Pydantic-validated, loopback CSRF-protected) | `202`, `400`, `403`, `409`, `413`, `415`, `422` |
| `GET` | `/api/news/refresh/status` | Poll live stage progress, metrics, and last refresh time | `200` |

---

## 1. `GET /api/health`

Returns basic liveness confirmation for the local API server.

**Example Request:**
```bash
curl http://127.0.0.1:8000/api/health
```

**Response (`200 OK`):**
```json
{
  "status": "ok",
  "service": "bangladesh-telecom-intel-api"
}
```

---

## 2. `POST /api/news/refresh`

Acquires the single-runner SQLite lock (`news_refresh`), creates a `refresh_jobs` record, and starts `NewsOrchestrator` asynchronously across RSS/Portals → Tavily → Local Ollama LLM → Daily Brief.

**Optional JSON Request Body (validated with Pydantic `RefreshRequest`, unknown fields rejected):**
```json
{
  "days": 14,
  "max_per_source": 30,
  "llm_batch_limit": 25,
  "run_tavily": true,
  "run_llm": true,
  "run_briefing": true
}
```
- Bounds & Strict Types: `days` (`1–90`, strict integer), `max_per_source` (`1–100`, strict integer), `llm_batch_limit` (`1–100`, strict integer), `run_tavily`/`run_llm`/`run_briefing` (strict booleans). Booleans or numeric strings are not coerced to integers.
- **CSRF, Content-Type & Size Limits:**
  - Requests with a non-loopback `Origin`/`Referer` header or `Sec-Fetch-Site: cross-site` are rejected with `403 Forbidden`.
  - Form/text `Content-Type`s (`application/x-www-form-urlencoded`, `multipart/form-data`, `text/plain`) and non-empty bodies without `Content-Type: application/json` are rejected with `415 Unsupported Media Type`.
  - Request bodies larger than **16 KB (16,384 bytes)** are rejected before JSON parsing with `413 Payload Too Large`.
  - Malformed or non-object JSON returns `400 Bad Request`; out-of-range, unknown, or non-strict field types return `422 Unprocessable Entity`.

**Example Request:**
```bash
curl -X POST http://127.0.0.1:8000/api/news/refresh \
  -H "Content-Type: application/json" \
  -d '{"days": 14, "llm_batch_limit": 25}'
```

**Response — Job Started (`202 Accepted`):**
```json
{
  "message": "News refresh started.",
  "job": {
    "is_running": true,
    "job_id": "refresh-20261125-222906-85c3e7",
    "status": "RUNNING",
    "current_stage": "rss_collection",
    "stages": [
      {
        "id": "rss_collection",
        "label": "1. RSS & Portal Collection (Extract, Normalize & Deduplicate)",
        "status": "running",
        "detail": "",
        "error": null
      }
    ],
    "metrics": {
      "discovered": 0,
      "extracted": 0,
      "deduplicated": 0,
      "processed": 0,
      "saved": 0,
      "tavily_credits_used": 0.0
    },
    "started_at": "2026-11-25T22:29:06+06:00",
    "finished_at": null,
    "error_message": null,
    "last_successful_refresh_at": "2026-11-25T22:32:44+06:00"
  }
}
```

**Response — Job Already Running (`409 Conflict`):**
```json
{
  "message": "A refresh job is already running.",
  "job": { "...active job snapshot..." }
}
```

---

## 3. `GET /api/news/refresh/status`

Returns the latest refresh job snapshot (or a specific job via `?job_id=<id>`). If no refresh job has ever been run, returns `status: "IDLE"` along with `last_successful_refresh_at` (falling back to the latest `scrape_runs.finished_at` if present).

**Example Request:**
```bash
curl http://127.0.0.1:8000/api/news/refresh/status
```

**Response (`200 OK`):**
```json
{
  "is_running": false,
  "job_id": "refresh-20261125-222906-85c3e7",
  "status": "COMPLETED",
  "current_stage": null,
  "stages": [
    {
      "id": "rss_collection",
      "label": "1. RSS & Portal Collection (Extract, Normalize & Deduplicate)",
      "status": "completed",
      "detail": "Discovered 13574, saved 0 new (73 duplicates skipped)",
      "error": null
    },
    {
      "id": "tavily_discovery",
      "label": "2. Tavily Search & Extract Fallback",
      "status": "completed",
      "detail": "0 queries (6 on cooldown), saved 0 new (0.0 credits)",
      "error": null
    },
    {
      "id": "llm_analysis",
      "label": "3. Local Ollama Analysis (Summary, Sentiment, Topics & Entities)",
      "status": "completed",
      "detail": "Analyzed 25 articles (0 failed) with qwen2.5:3b",
      "error": null
    },
    {
      "id": "briefing_clustering",
      "label": "4. Bangla–English Clustering & Daily Brief",
      "status": "completed",
      "detail": "Daily Brief COMPLETED (5 consolidated developments)",
      "error": null
    }
  ],
  "metrics": {
    "discovered": 13574,
    "extracted": 9,
    "deduplicated": 73,
    "processed": 25,
    "saved": 0,
    "tavily_credits_used": 0.0
  },
  "started_at": "2026-11-25T22:29:06+06:00",
  "finished_at": "2026-11-25T22:42:08+06:00",
  "error_message": null,
  "last_successful_refresh_at": "2026-11-25T22:42:08+06:00"
}
```

### Possible Job Status Values
- `IDLE` — No refresh job recorded yet.
- `RUNNING` — Background worker actively executing stages.
- `COMPLETED` — All enabled stages succeeded.
- `PARTIAL_FAILURE` — At least one stage failed (isolated) while other stages succeeded and preserved their data.
- `FAILED` — All enabled stages failed.
