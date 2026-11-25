# Security Policy

## Credential & Secret Handling

- **Never commit secrets:** Keep `.env` strictly local. `.gitignore` excludes `.env` and `.env.*` while preserving the sanitized `.env.example` template.
- **Placeholder policy:** `.env.example` and tests must contain only non-functional placeholder strings.
- **API key rotation:** If a Tavily API key (`TAVILY_API_KEY`) or Meta Graph API token (`META_ACCESS_TOKEN`) is ever exposed in logs, chat, or shell history, rotate it immediately in the respective provider dashboard and update your local `.env`.
- **Local-only inference enforcement:** `bd_telecom_scraper/llm/config.py` explicitly validates that `OLLAMA_BASE_URL` resolves to a loopback host (`localhost`, `127.0.0.1`, or `::1`), preventing accidental transmission of scraped text to external LLM endpoints.
- **Loopback API binding:** The Starlette/Uvicorn refresh API binds strictly to `127.0.0.1` by default and includes no authentication middleware; do **not** expose port `8000` or Streamlit port `8501` directly to untrusted networks without placing them behind an authenticated reverse proxy.

## Data Privacy & Compliance

- The scraper collects only publicly accessible organizational news and explicitly honors `robots.txt` and `HTTP 403` restrictions without attempting anti-bot bypasses.
- The Facebook module avoids collecting personal user profiles or private group content and provides `purge` CLI commands (`--source-id`, `--older-than-days`) to support data retention and deletion requests.
- Collected news exports (`data/*.db`, `data/*.csv`, `data/*.json`) are gitignored and must not be committed to public repositories.

## Reporting a Vulnerability

If you discover a security issue in this repository:
1. Do **not** open a public GitHub issue containing exploit details or exposed secrets.
2. Report it privately via GitHub Security Advisories (once published) or directly to the repository maintainer with reproduction steps and affected file paths.
3. Expect an acknowledgement and remediation plan for verified security defects.
