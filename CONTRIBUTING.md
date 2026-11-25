# Contributing to Bangladesh Telecom Intelligence

Thank you for your interest in contributing. This project prioritizes **factual reliability, publisher/platform compliance, local-first execution, and clean modular Python**.

## Development Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## Non-Negotiable Engineering Rules

1. **Compliance First:** Never add anti-bot/Cloudflare bypasses, CAPTCHA solvers, unauthorized browser scraping, fake logins, or cookie extraction. All news crawlers must respect `robots.txt` and per-domain rate limits.
2. **Local-Only LLM Guarantee:** Do not introduce paid or cloud LLM APIs into `bd_telecom_scraper/llm/`. Keep `OLLAMA_BASE_URL` restricted to loopback hosts.
3. **No Fabricated Metadata or Facts:** Missing dates, engagement metrics, or Page IDs must remain `None`/`null`. LLM prompts and Pydantic schemas must strictly distinguish reported facts from AI interpretation.
4. **Offline Deterministic Tests:** All new features or bug fixes must include unit tests under `bd_telecom_scraper/tests/` that run offline without `.env`, live network calls, or a running Ollama server.
5. **No Secrets or Scraped Data in Commits:** Never commit `.env`, API keys, SQLite databases, or exported news CSV/JSON files.

## Pull Request Checklist

- [ ] Code changes are scoped, typed, and follow existing module boundaries.
- [ ] All tests pass via `.venv/bin/pytest`.
- [ ] Any new environment variables are documented in `.env.example` and `docs/SETUP_AND_INSTALLATION.md`.
- [ ] Documentation under `docs/` and `README.md` accurately reflects actual code behavior.
