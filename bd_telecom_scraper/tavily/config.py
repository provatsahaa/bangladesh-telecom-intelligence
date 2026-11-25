"""Configuration & safe .env loader for Tavily Search & Extract integration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv_file(env_path: str | Path = ".env") -> None:
    """Load KEY=VALUE pairs from a local .env file without overriding existing env vars."""
    path = Path(env_path)
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


def _parse_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid integer for environment variable {name}: {raw!r}") from exc


def _parse_env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid float for environment variable {name}: {raw!r}") from exc


_PLACEHOLDER_MARKERS = (
    "your_",
    "replace_",
    "changeme",
    "placeholder",
    "<your",
    "example",
)


def is_usable_credential(value: str | None) -> bool:
    """Return True only if credential is non-empty and not an obvious template placeholder."""
    cleaned = (value or "").strip()
    if not cleaned:
        return False
    low = cleaned.lower()
    if low.endswith("_here") or any(low.startswith(m) for m in _PLACEHOLDER_MARKERS):
        return False
    return True


@dataclass(frozen=True)
class TavilyConfig:
    """Runtime settings for Tavily discovery, extraction fallback, and credit controls."""

    api_key: str = ""
    base_url: str = "https://api.tavily.com"
    search_depth: str = "basic"  # "basic" (1 credit) or "advanced" (2 credits)
    extract_depth: str = "basic"  # "basic" (1 credit / 5 URLs)
    max_results_per_query: int = 10
    min_interval_hours: float = 12.0
    monthly_credit_budget: int = 800
    timeout_seconds: float = 25.0
    enable_extract_fallback: bool = True

    @property
    def search_credit_cost(self) -> int:
        return 2 if self.search_depth == "advanced" else 1

    @property
    def has_api_key(self) -> bool:
        return is_usable_credential(self.api_key)


def load_tavily_config(
    *,
    env_path: str | Path = ".env",
    api_key_override: str | None = None,
    budget_override: int | None = None,
    min_interval_override: float | None = None,
) -> TavilyConfig:
    """Load TavilyConfig from `.env` and environment variables."""
    load_dotenv_file(env_path)
    depth = (os.getenv("TAVILY_SEARCH_DEPTH") or "").strip().lower() or "basic"
    if depth not in ("basic", "advanced"):
        depth = "basic"
    base_url = (os.getenv("TAVILY_BASE_URL") or "").strip().rstrip("/") or "https://api.tavily.com"
    extract_depth = (os.getenv("TAVILY_EXTRACT_DEPTH") or "").strip().lower() or "basic"
    return TavilyConfig(
        api_key=(api_key_override if api_key_override is not None else (os.getenv("TAVILY_API_KEY") or "")).strip(),
        base_url=base_url,
        search_depth=depth,
        extract_depth=extract_depth,
        max_results_per_query=_parse_env_int("TAVILY_MAX_RESULTS", 10),
        min_interval_hours=(
            min_interval_override
            if min_interval_override is not None
            else _parse_env_float("TAVILY_MIN_INTERVAL_HOURS", 12.0)
        ),
        monthly_credit_budget=(
            budget_override
            if budget_override is not None
            else _parse_env_int("TAVILY_MONTHLY_CREDIT_BUDGET", 800)
        ),
        timeout_seconds=_parse_env_float("TAVILY_TIMEOUT_SECONDS", 25.0),
        enable_extract_fallback=(os.getenv("TAVILY_EXTRACT_FALLBACK") or "true").strip().lower() != "false",
    )
