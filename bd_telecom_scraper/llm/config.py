"""Central configuration for the local-only Ollama LLM intelligence engine."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

PROMPT_VERSION = "v1.0"
BRIEFING_PROMPT_VERSION = "brief-v1.0"

_ALLOWED_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class LLMConfig:
    """Configuration for local Ollama inference, batching, and reliability."""

    base_url: str = "http://localhost:11434"
    model_name: str = "qwen2.5:3b"
    temperature: float = 0.1
    num_ctx: int = 4096
    max_input_chars: int = 6000
    timeout_seconds: float = 120.0
    batch_size: int = 10
    max_retries: int = 2
    prompt_version: str = PROMPT_VERSION
    briefing_prompt_version: str = BRIEFING_PROMPT_VERSION

    def validate_loopback(self) -> None:
        """Enforce strictly local execution (zero cloud LLM APIs)."""
        parsed = urlparse(self.base_url)
        host = (parsed.hostname or "").lower()
        if host not in _ALLOWED_LOOPBACK_HOSTS:
            raise ValueError(
                f"Local-only policy violation: OLLAMA_BASE_URL host '{host}' is not loopback."
            )


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


def load_llm_config(
    *,
    model_override: str | None = None,
    batch_size_override: int | None = None,
) -> LLMConfig:
    """Load LLM configuration from environment variables with safe local defaults."""
    base_url = (os.getenv("OLLAMA_BASE_URL") or "").strip() or "http://localhost:11434"
    model_name = (model_override or os.getenv("OLLAMA_MODEL") or "").strip() or "qwen2.5:3b"
    cfg = LLMConfig(
        base_url=base_url,
        model_name=model_name,
        temperature=_parse_env_float("LLM_TEMPERATURE", 0.1),
        num_ctx=_parse_env_int("LLM_NUM_CTX", 4096),
        max_input_chars=_parse_env_int("LLM_MAX_INPUT_CHARS", 6000),
        timeout_seconds=_parse_env_float("LLM_TIMEOUT_SECONDS", 120.0),
        batch_size=(
            batch_size_override
            if batch_size_override is not None
            else _parse_env_int("LLM_BATCH_SIZE", 10)
        ),
        max_retries=_parse_env_int("LLM_MAX_RETRIES", 2),
    )
    cfg.validate_loopback()
    return cfg
