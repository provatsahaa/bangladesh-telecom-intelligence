# `bd_telecom_scraper` Package Overview

`bd_telecom_scraper` is the core Python package powering the **Bangladesh Telecom Intelligence** platform.

## Subpackages & Modules

- **Core News Scraper** (`sources.py`, `compliance.py`, `discovery.py`, `extractor.py`, `filtering.py`, `deduplicator.py`, `storage.py`, `pipeline.py`, `cli.py`) — Compliance-first collection across 18 Bangladeshi news sources (Quintype APIs, RSS, XML sitemaps, permitted HTML).
- **`tavily/`** — Tavily Search (`POST /search`) discovery with credible domain filtering, `POST /extract` fallback, pre-DB duplicate suppression, cooldowns, and monthly credit budget enforcement.
- **`facebook/`** — Verified 24-Page telecom registry, official Meta Graph API `v27.0` client, manual public post ingestion, and `unified_telecom_intel` SQL view.
- **`llm/`** — Local-only Ollama intelligence engine (`qwen2.5:3b`), Pydantic v2 structured output schemas, bilingual entity fusion, benchmarking harness, and Daily Brief clustering engine.
- **`orchestrator.py` & `api/`** — Centralized asynchronous `NewsOrchestrator` and local Starlette + Uvicorn REST API (`127.0.0.1:8000`).
- **`dashboard/`** — 4-section editorial Streamlit dashboard (`127.0.0.1:8501`) with one-click **Fetch Latest News**.
- **`tests/`** — Offline deterministic unit and API integration tests (`pytest`).

For complete setup, architecture, pipeline, and API documentation, see the root [README.md](../README.md) and the [`docs/`](../docs/) directory.
