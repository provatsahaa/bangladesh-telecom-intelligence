# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-26

### Added
- **Compliance-first Bangladeshi news scraper (`bd_telecom_scraper/`):** 18 configured news portal editions supporting official Quintype Search & Collections JSON APIs, live RSS feeds, 14-day XML sitemaps, and permitted HTML fallbacks with `robots.txt` enforcement, per-domain rate limiting, bilingual (English & Bangla) telecom filtering, entity normalization, syndicated deduplication, and SQLite/JSON/CSV storage.
- **Tavily Search & Extract module (`bd_telecom_scraper/tavily/`):** 6 semantic Bangladesh and global telecom queries with credible domain filtering, pre-extraction duplicate suppression, `POST /extract` fallback for failed HTML extractions, per-query cooldowns, and monthly free-tier credit budget tracking in SQLite.
- **Compliant Facebook Page intelligence module (`bd_telecom_scraper/facebook/`):** Verified 24-Page registry, Meta Graph API `v27.0` client with pagination and rate-limit header throttling, manual public post single/batch ingestion, `unified_telecom_intel` SQL view, and retention `purge` commands.
- **Local Ollama LLM & Daily Brief engine (`bd_telecom_scraper/llm/`):** Strictly loopback Ollama client (`qwen2.5:3b` default, benchmarked against `gemma3:1b` on 8 GB M1), Pydantic v2 structured output schemas separating reported facts from AI implications, deterministic bilingual entity fusion, incremental `content_hash` processing, failure isolation, and Bangla–English event clustering for executive Daily Briefs.
- **One-Click News Refresh (`orchestrator.py`, `api/server.py`, `scripts/start_telecom_intel.sh`):** Asynchronous `NewsOrchestrator` with SQLite single-runner locking, stage retries, partial-failure isolation, and Starlette + Uvicorn REST API (`GET /api/health`, `POST /api/news/refresh`, `GET /api/news/refresh/status`).
- **Editorial Intelligence Dashboard (`bd_telecom_scraper/dashboard/`):** 4-section Streamlit UI (Editorial Masthead with live `@st.fragment` refresh monitor, The Daily Brief, Lead Story + Filtered Intelligence Feed, and 7-day vs. prior-7-day Industry Signals) plus collapsible System & Operations sidebar.
- **Testing, Packaging & CI:** 26 deterministic offline unit & API tests (`pytest`), Apache License 2.0, GitHub Actions CI workflow (`.github/workflows/ci.yml`), and full documentation suite under `docs/`.
