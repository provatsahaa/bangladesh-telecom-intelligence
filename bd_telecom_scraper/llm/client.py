"""Local Ollama inference client using the official Python `ollama` library."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TypeVar

import ollama
from pydantic import BaseModel, ValidationError

from .config import LLMConfig

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class OllamaUnavailableError(RuntimeError):
    """Raised when the local Ollama daemon cannot be reached."""


class StructuredLLMError(RuntimeError):
    """Raised when structured output validation fails after all retries."""


def _extract_json_payload(raw_text: str) -> str:
    """Strip markdown fences or stray prose around a JSON object if present."""
    cleaned = (raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned
    m = _JSON_BLOCK_RE.search(cleaned)
    return m.group(0) if m else cleaned


class LocalOllamaClient:
    """Wrapper around `ollama.Client` with strict Pydantic structured-output validation."""

    def __init__(self, config: LLMConfig) -> None:
        config.validate_loopback()
        self.config = config
        self._client = ollama.Client(
            host=config.base_url,
            timeout=config.timeout_seconds,
        )

    def check_health(self) -> tuple[bool, list[str], str | None]:
        """Return (is_connected, installed_model_names, error_message)."""
        try:
            resp = self._client.list()
            models: list[str] = []
            raw_models = getattr(resp, "models", None) or (resp.get("models", []) if isinstance(resp, dict) else [])
            for m in raw_models:
                name = getattr(m, "model", None) or (m.get("model") or m.get("name") if isinstance(m, dict) else None)
                if name:
                    models.append(str(name))
            return True, models, None
        except Exception as exc:
            return False, [], f"{type(exc).__name__}: {exc}"

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_cls: type[T],
        model_override: str | None = None,
    ) -> tuple[T, int, int]:
        """Call Ollama with JSON schema constraint and validate with Pydantic.

        Returns:
            (validated_model, latency_ms, attempts_used)
        """
        model_name = model_override or self.config.model_name
        json_schema = schema_cls.model_json_schema()
        last_error = ""
        start_total = time.monotonic()

        for attempt in range(1, self.config.max_retries + 2):
            try:
                response = self._client.chat(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    format=json_schema,
                    options={
                        "temperature": self.config.temperature,
                        "num_ctx": self.config.num_ctx,
                    },
                )
            except Exception as exc:
                msg = str(exc)
                if "connection" in msg.lower() or "refused" in msg.lower() or "connect" in msg.lower():
                    raise OllamaUnavailableError(
                        f"Cannot connect to local Ollama at {self.config.base_url}: {exc}"
                    ) from exc
                last_error = f"Ollama call failed (attempt {attempt}): {type(exc).__name__}: {exc}"
                logger.warning(last_error)
                continue

            raw_content = ""
            if hasattr(response, "message") and response.message is not None:
                raw_content = getattr(response.message, "content", "") or ""
            elif isinstance(response, dict):
                raw_content = str(response.get("message", {}).get("content", ""))

            try:
                json_str = _extract_json_payload(raw_content)
                data = json.loads(json_str)
                validated = schema_cls.model_validate(data)
                elapsed_ms = int((time.monotonic() - start_total) * 1000)
                return validated, elapsed_ms, attempt
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                last_error = f"Validation failed (attempt {attempt}): {type(exc).__name__}: {str(exc)[:300]}"
                logger.warning(last_error)

        raise StructuredLLMError(last_error or "Structured output generation failed.")
