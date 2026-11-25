# Setup & Installation Guide

Tested on **macOS (Apple Silicon M1, 8 GB RAM, Python 3.13)** and compatible with Linux / macOS Python 3.11+ environments.

---

## 1. Prerequisites

- **Python 3.11+** (`python3 --version`)
- **Ollama** (required for local LLM analysis and Daily Brief generation; not required to run the scraper or offline tests):
  - macOS / Linux: install from [ollama.com](https://ollama.com)
  - Pull the default benchmarked model (~1.9 GB):
    ```bash
    ollama pull qwen2.5:3b
    ```
  - Verify Ollama is running locally (`http://localhost:11434`):
    ```bash
    ollama list
    ```

---

## 2. Clone & Install Dependencies

```bash
# Create an isolated virtual environment
python3 -m venv .venv

# Recommended: editable install from pyproject.toml (installs package + `bd-telecom-*` CLI entry points + pytest)
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
```

> **Difference between `pip install -e ".[dev]"` and `pip install -r requirements.txt`:**
> - **`pip install -e ".[dev]"` (Recommended):** Installs all runtime/dev dependencies **plus** registers the `bd_telecom_scraper` package and console entry points (`bd-telecom-scraper`, `bd-telecom-llm`, `bd-telecom-tavily`, `bd-telecom-facebook`, `bd-telecom-api`).
> - **`pip install -r requirements.txt`:** Installs third-party dependencies only (no console entry points); run modules with `PYTHONPATH=. .venv/bin/python -m bd_telecom_scraper ...`.

---

## 3. Environment Configuration

Copy the sanitized template to create your local `.env` (gitignored):

```bash
cp .env.example .env
```

Optional API keys (`TAVILY_API_KEY` and `META_ACCESS_TOKEN`) are blank by default in `.env.example` so a fresh clone skips optional external services cleanly without making unauthenticated API calls.

### Important: How Modules Load Environment Variables
- **`scripts/start_telecom_intel.sh`** safely parses `.env` as literal `KEY=VALUE` pairs (without shell execution via `source`/`eval`) and preserves any caller-exported overrides before starting both the local API (`TELECOM_API_PORT`, default `8000`) and Streamlit (`STREAMLIT_SERVER_PORT`, default `8501`), polling `/api/health` until ready.
- **`bd_telecom_scraper/tavily/config.py`** also auto-loads `.env` directly via `load_dotenv_file()` without overwriting existing environment variables.
- When invoking **`llm`**, **`api.server`**, or **`facebook`** standalone via Python CLI (outside `start_telecom_intel.sh`), export the desired environment variables before running the command.

### Optional vs. Required External Services

| Service | Required? | Behavior When Unconfigured |
| :--- | :---: | :--- |
| **SQLite** | Built-in | Automatically creates `data/bd_telecom_news.db` and all 11 tables + 1 view on first run. |
| **Local Ollama (`qwen2.5:3b`)** | Required for AI summaries & Daily Brief | Dashboard and scrapers still run; LLM stages fail gracefully with an `OllamaUnavailableError` notice. |
| **Tavily API (`TAVILY_API_KEY`)** | Optional (free tier: 1,000 credits/mo) | If blank, `NewsOrchestrator` skips Stage 2 cleanly and completes RSS + LLM + Briefing. |
| **Meta Graph API (`META_ACCESS_TOKEN`)** | Optional | If blank, Facebook Pages remain in `manual_only` ingestion mode (`ingest-manual` / `ingest-batch`). |

---

## 4. Verify Installation (Offline Tests)

Run the deterministic test suite (works with zero `.env`, no database, and no Ollama running):

```bash
.venv/bin/pytest
```

Expected result: all tests pass with zero failures.

---

## 5. Run the Platform

### One-Command Launch (Recommended)
Starts both the local Starlette/Uvicorn API (`http://127.0.0.1:8000`) and the Streamlit Editorial Dashboard (`http://127.0.0.1:8501`):

```bash
./scripts/start_telecom_intel.sh
```

Open **`http://127.0.0.1:8501`** in your browser and click **Fetch Latest News** to populate a fresh database or refresh an existing one. Press `Ctrl + C` in Terminal to stop both services cleanly.

### Direct Streamlit Launch
If you run Streamlit directly without the launcher script, `app.py` automatically boots the local API on `127.0.0.1:8000` in a background daemon thread:

```bash
.venv/bin/streamlit run bd_telecom_scraper/dashboard/app.py
```

### Troubleshooting
- **Ollama connection refused:** Ensure the Ollama desktop app is open or run `ollama serve` in a separate terminal.
- **Port 8000 already in use:** Set `export TELECOM_API_PORT=8001` before launching.
- **No articles yet on a brand-new clone:** The dashboard renders an honest empty state until you click **Fetch Latest News** or run `.venv/bin/python -m bd_telecom_scraper --days 14`.
