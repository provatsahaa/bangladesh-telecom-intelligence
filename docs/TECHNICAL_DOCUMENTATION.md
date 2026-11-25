# Technical Documentation — Bangladesh Telecom Intelligence

This reference documents the **actual code implementation** inside `bd_telecom_scraper/` (52 Python modules across 6 functional subsystems).

---

## 1. Core News Scraper (`bd_telecom_scraper/`)

- **`sources.py` (`DEFAULT_SOURCES`):** Configures 18 Bangladeshi news editions across 15 publishers (Prothom Alo EN/BN, The Daily Star, Dhaka Tribune, The Business Standard EN/BN, The Financial Express, The Daily Ittefaq EN/BN, bdnews24, New Age, Bangla Tribune, Techshohor, Bonik Barta, BSS News, plus Cloudflare-restricted Jugantor, Kaler Kantho, and Samakal).
- **`compliance.py` (`PoliteHttpClient`):** Wraps `httpx.Client` with cached `urllib.robotparser.RobotFileParser` per domain, monotonic per-domain rate limiting (`rate_limit_seconds`), 1 retry on transient `5xx`/`429`, and explicit non-bypass handling for `HTTP 403` (`blocked_reason="http_403_waf"`).
- **`discovery.py` (`SourceDiscoverer`):** Executes discovery in priority order:
  1. Official Quintype Collections (`/api/v1/collections/*`) & Search (`/api/v1/search?q=...`) JSON APIs for Prothom Alo EN/BN and bdnews24.
  2. Live RSS/Atom feeds via `feedparser` (using live section feeds where root `/rss.xml` is stale on Daily Star and TBS).
  3. XML sitemaps with smart child selection (`_select_relevant_child_sitemaps`) targeting `news-sitemap`/`googlenews.xml`, daily sitemaps (`YYYY-MM-DD`), monthly sitemaps (`YYYY-MM-01.xml`), and latest half-year sitemaps within the 14-day window.
  4. Permitted HTML section fallback via `BeautifulSoup` constrained by `allowed_path_hints`.
  - All timestamps normalize to `Asia/Dhaka` (`UTC+06:00`) via `parse_datetime_flexible()`.
- **`extractor.py` (`extract_article_from_html`):** Extracts metadata and body text in fallback order: embedded JSON-LD (`NewsArticle`/`Article`) → OpenGraph/meta tags → `trafilatura.extract(..., favor_precision=True)` → BeautifulSoup `<article><p>` paragraphs. Filters site-wide boilerplate meta descriptions (e.g., generic newspaper taglines) back to article lead text.
- **`filtering.py` (`evaluate_relevance`, `extract_entities`, `candidate_looks_promising`):**
  - Fast pre-filter (`candidate_looks_promising`) uses word-bounded slug regexes (`(?<![a-z0-9])hint(?![a-z0-9])`) plus URL-decoded Bangla slug hints to avoid false hits on random alphanumeric story IDs.
  - Weighted bilingual scoring (`TOPIC_RULES`, headline matches boosted `1.5x`, default threshold `2.0`) across MNOs, BTRC/regulation, 2G–6G/RAN/infrastructure, vendors (`Huawei`, `Ericsson`, `Nokia`, `ZTE`), broadband/MFS, and telecom AI.
  - Canonical entity normalizer (`ENTITY_ALIASES`) maps English and Bangla aliases to canonical organization names.
- **`deduplicator.py` (`Deduplicator`):** Strips tracking parameters (`utm_*`, `fbclid`, `gclid`, `m.` mobile prefixes), hashes normalized headline + first 1,500 body characters (`content_hash`), and catches syndicated wire stories using `SequenceMatcher` headline similarity (`>= 0.86`).
- **`storage.py` (`ArticleStore`):** Manages SQLite `articles` and `scrape_runs` tables (`INSERT OR IGNORE` on `canonical_url`), plus UTF-8 `export_json()` and `export_csv()`.

---

## 2. Tavily Search & Extract Module (`bd_telecom_scraper/tavily/`)

- **`config.py` (`load_tavily_config`):** Loads local `.env` via a zero-dependency `load_dotenv_file()` helper without overwriting already-exported environment variables. Defaults to `search_depth="basic"` (1 credit), `extract_depth="basic"` (1 credit per 5 successful URLs), `min_interval_hours=12.0`, and `monthly_credit_budget=800`.
- **`queries.py` (`DEFAULT_TAVILY_QUERIES`):** 4 Bangladesh telecom semantic queries (`include_domains = BANGLADESH_CREDIBLE_DOMAINS`) and 2 global telecom queries (`include_domains = GLOBAL_TELECOM_CREDIBLE_DOMAINS`), excluding social/wiki domains.
- **`client.py` (`TavilyRestClient`):** Calls `POST https://api.tavily.com/search` (`topic="news"`) and `POST https://api.tavily.com/extract` directly via `httpx` with `Authorization: Bearer <TAVILY_API_KEY>`.
- **`usage.py` (`TavilyUsageTracker`):** Enforces `can_spend()` against the current `YYYY-MM` sum in `tavily_usage_log` and `is_query_due()` against `tavily_query_state`.
- **`pipeline.py` (`TavilyDiscoveryPipeline`):** Checks every search hit against existing `articles` (`Deduplicator`) before fetching; attempts free `PoliteHttpClient` HTML extraction first; batches only failed/short URLs (5 at a time) into `POST /extract`; runs `evaluate_relevance()` and persists directly into `articles` (`discovery_method = 'tavily_search'` or `'tavily_extract'`).

---

## 3. Facebook Page Intelligence Module (`bd_telecom_scraper/facebook/`)

- **`source_registry.py` (`DEFAULT_FACEBOOK_SOURCES`):** 24 target Pages (4 MNOs, 5 regulators/orgs, 5 telecom/tech media including unverified `Cablegram`, 10 Bangladeshi news publishers). Each record stores `verification_status` (`VERIFIED_OFFICIAL_SITE`, `VERIFIED_SECONDARY`, `UNVERIFIED_NEEDS_CONFIRMATION`), `verification_evidence`, and `access_mode` (defaults to `manual_only`).
- **`graph_client.py` (`MetaGraphClient`):** Official Meta Graph API `v27.0` client using explicit field declarations (`POST_FIELDS`), cursor pagination (`paging.next`), `since`/`until` Unix windows, `X-App-Usage`/`X-Page-Usage` throttling (`>= 75%` pauses 5–15s), and graceful `PERMISSION_REQUIRED` handling on Graph error codes `{10, 100, 190, 200, 283}`. Never circumvents access controls.
- **`manual_ingest.py`:** Validates public `facebook.com` / `fb.watch` URLs, auto-matches verified registry Pages, detects `en`/`bn`/`mixed` language, and supports single CLI or JSON/CSV batch import. Missing fields (`page_id`, `post_id`, `engagement`, `media_refs`) remain `None` (never fabricated).
- **`storage.py` (`FacebookStore`):** Creates `facebook_sources`, `facebook_posts`, `facebook_sync_state`, and the `unified_telecom_intel` SQL view, plus `purge_source()` and `purge_older_than()` for data-retention compliance.

---

## 4. Local Ollama LLM & Daily Brief Engine (`bd_telecom_scraper/llm/`)

- **`config.py` (`load_llm_config`):** Reads `OLLAMA_BASE_URL`, `OLLAMA_MODEL` (`qwen2.5:3b`), `LLM_TEMPERATURE` (`0.1`), `LLM_NUM_CTX` (`4096`), `LLM_MAX_INPUT_CHARS` (`6000`), `LLM_TIMEOUT_SECONDS` (`120`), `LLM_BATCH_SIZE` (`10`), `LLM_MAX_RETRIES` (`2`). Calls `validate_loopback()`.
- **`schemas.py`:** Strict Pydantic v2 models (`ArticleIntelligence`, `BriefingDevelopment`, `DailyBriefingOutput`):
  - Enforces the 12 canonical telecom categories (`TELECOM_CATEGORIES`), normalized operator names (`GP → Grameenphone`, `Robi Axiata → Robi`), confidence normalization (`1–10` or `1–100` scaled into `0.0–1.0`), and minimum substantive takeaways.
- **`prompts.py`:** Versioned prompts (`PROMPT_VERSION = "v1.0"`, `BRIEFING_PROMPT_VERSION = "brief-v1.0"`) enforcing factual grounding, exact Tk/MHz/percentage preservation, and separation of reported facts vs. AI interpretation.
- **`client.py` (`LocalOllamaClient`):** Uses official `ollama.Client`, passes `format=schema_cls.model_json_schema()`, strips stray markdown fences via `_extract_json_payload()`, retries on validation errors, and raises `OllamaUnavailableError` cleanly when Ollama is down.
- **`repository.py` (`IntelligenceRepository`):** Manages `article_ai_analysis`, `daily_briefings`, and `llm_job_locks` (with 30–45 minute stale lock reclamation). `get_eligible_articles()` selects only articles with no analysis, `PENDING` status, changed `content_hash`, or retryable `FAILED` status (`retry_count <= max_retries`).
- **`pipeline.py` (`ArticleIntelligencePipeline`):** Processes batches with `PENDING → PROCESSING → COMPLETED | FAILED` transitions, isolates per-article errors, and fuses deterministic `extract_entities()` output into `organizations` and `operators_mentioned` to prevent small-model omission or hallucination.
- **`briefing.py` (`DailyBriefingEngine`):** Loads `COMPLETED` articles (`High`/`Medium` relevance) for the target date (falling back to a 7-day rolling window if the exact date has `< 2` articles), clusters related Bangla and English articles via `cluster_related_articles()` (`SequenceMatcher >= 0.62`, headline token Jaccard `>= 0.42`, or shared category + operator + `>= 2` overlapping tokens), generates `DailyBriefingOutput`, strips any invalid/hallucinated `supporting_article_ids`, and caches by `source_fingerprint`.
- **`benchmark.py`:** Reproducible multi-model benchmark measuring success rate, first-pass JSON compliance, latency, and `psutil` system memory delta on a stratified Bangla + English sample.

---

## 5. Orchestrator, Starlette API & Editorial Dashboard

- **`orchestrator.py` (`NewsOrchestrator`):** Coordinates 4 sequential stages (`rss_collection`, `tavily_discovery`, `llm_analysis`, `briefing_clustering`) in a background daemon thread protected by both an in-process `threading.Lock` and SQLite `llm_job_locks`. Retries transient stage errors (`stage_retries=1`), isolates partial failures (`PARTIAL_FAILURE` vs. `FAILED`), updates `refresh_jobs` after each stage, and logs to `logs/news_refresh.log` (`RotatingFileHandler`, 2 MB × 3 backups).
- **`api/server.py`:** Local Starlette + Uvicorn ASGI app (`127.0.0.1:8000`) exposing `GET /api/health`, `POST /api/news/refresh`, and `GET /api/news/refresh/status`. Includes `ensure_api_server_running()` so direct Streamlit launches also have a reachable local API.
- **`dashboard/data_service.py` (`DashboardDataService`):** Executes targeted SQLite queries (`LIMIT`/`OFFSET` pagination, filter option lookups, lead-story ranking, and 7-day vs. prior-7-day topic/operator momentum signals) without loading the entire database into memory.
- **`dashboard/app.py` & `styles.py`:** Warm-ivory editorial Streamlit UI with 4 sections (Editorial Masthead + **Fetch Latest News** button + `@st.fragment(run_every=2)` live progress strip, The Daily Brief, Lead Story + Filtered Intelligence Feed with expandable analysis drawers, and Industry Signals) plus a collapsible System & Operations sidebar.

---

## 6. Testing Strategy (`bd_telecom_scraper/tests/`)

13 deterministic offline tests across 5 files run via `pytest` (`pyproject.toml` sets `testpaths = ["bd_telecom_scraper/tests"]` and `pythonpath = ["."]`), covering:
- `test_scraper.py`: bilingual relevance, entity extraction, URL canonicalization, syndicated dedup, 14-day sitemap filtering, JSON-LD extraction, SQLite/JSON/CSV exports.
- `test_tavily_module.py`: mocked `/search` + `/extract` fallback, duplicate suppression, cooldown, monthly credit budget cap.
- `test_facebook_module.py`: 24-Page registry, mocked Graph API `v27.0` pagination & PPCA `(#10)` denial, manual single/batch ingestion, `unified_telecom_intel` view, retention purge.
- `test_llm_and_dashboard.py`: incremental processing, short-article isolation, Ollama unavailable / malformed JSON handling, briefing clustering & ID sanitization, dashboard filters & signals.
- `test_orchestrator_and_api.py`: `202`/`409` concurrency lock via Starlette `TestClient`, live status polling, partial-failure stage isolation.
