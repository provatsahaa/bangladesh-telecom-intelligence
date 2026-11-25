"""Lightweight REST client for Tavily Search & Extract APIs using existing `httpx`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import TavilyConfig


@dataclass
class TavilySearchHit:
    """Single article candidate returned by Tavily Search API."""

    url: str
    title: str
    content: str
    published_date: str | None = None
    score: float = 0.0


@dataclass
class TavilyExtractResult:
    """Extracted full text returned by Tavily Extract API for a single URL."""

    url: str
    raw_content: str


@dataclass
class TavilyResponse:
    """Generic API response wrapper."""

    ok: bool
    search_hits: list[TavilySearchHit] = field(default_factory=list)
    extract_results: list[TavilyExtractResult] = field(default_factory=list)
    error: str | None = None
    status_code: int | None = None


class TavilyRestClient:
    """Official Tavily REST API client (`POST /search` and `POST /extract`)."""

    def __init__(
        self,
        config: TavilyConfig,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=config.timeout_seconds)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> TavilyRestClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def search(
        self,
        *,
        query: str,
        days: int = 14,
        max_results: int | None = None,
        include_domains: tuple[str, ...] = (),
        exclude_domains: tuple[str, ...] = (),
    ) -> TavilyResponse:
        """Execute `POST /search` with news topic and credible domain filtering."""
        if not self.config.has_api_key:
            return TavilyResponse(ok=False, error="TAVILY_API_KEY is not configured in .env.")

        payload: dict[str, Any] = {
            "query": query,
            "topic": "news",
            "search_depth": self.config.search_depth,
            "days": max(1, days),
            "max_results": max_results or self.config.max_results_per_query,
            "include_answer": False,
            "include_images": False,
            "include_raw_content": False,
        }
        if include_domains:
            payload["include_domains"] = list(include_domains)
        if exclude_domains:
            payload["exclude_domains"] = list(exclude_domains)

        try:
            resp = self._client.post(
                f"{self.config.base_url}/search",
                headers=self._headers(),
                json=payload,
            )
        except httpx.HTTPError as exc:
            return TavilyResponse(ok=False, error=f"Network error: {type(exc).__name__}: {exc}")

        if resp.status_code != 200:
            return TavilyResponse(
                ok=False,
                status_code=resp.status_code,
                error=f"HTTP {resp.status_code}: {resp.text[:250]}",
            )

        data = resp.json()
        hits: list[TavilySearchHit] = []
        for item in data.get("results", []):
            if not isinstance(item, dict) or not item.get("url"):
                continue
            hits.append(
                TavilySearchHit(
                    url=str(item["url"]).strip(),
                    title=str(item.get("title") or "").strip(),
                    content=str(item.get("content") or "").strip(),
                    published_date=str(item.get("published_date") or "").strip() or None,
                    score=float(item.get("score") or 0.0),
                )
            )
        return TavilyResponse(ok=True, status_code=200, search_hits=hits)

    def extract(self, urls: list[str]) -> TavilyResponse:
        """Execute `POST /extract` for up to 20 URLs as a fallback when HTML extraction fails."""
        if not self.config.has_api_key:
            return TavilyResponse(ok=False, error="TAVILY_API_KEY is not configured in .env.")
        if not urls:
            return TavilyResponse(ok=True, status_code=200, extract_results=[])

        payload: dict[str, Any] = {
            "urls": urls[:20],
            "extract_depth": self.config.extract_depth,
            "include_images": False,
        }
        try:
            resp = self._client.post(
                f"{self.config.base_url}/extract",
                headers=self._headers(),
                json=payload,
            )
        except httpx.HTTPError as exc:
            return TavilyResponse(ok=False, error=f"Network error: {type(exc).__name__}: {exc}")

        if resp.status_code != 200:
            return TavilyResponse(
                ok=False,
                status_code=resp.status_code,
                error=f"HTTP {resp.status_code}: {resp.text[:250]}",
            )

        data = resp.json()
        extracted: list[TavilyExtractResult] = []
        for item in data.get("results", []):
            if not isinstance(item, dict) or not item.get("url"):
                continue
            raw_content = str(item.get("raw_content") or item.get("content") or "").strip()
            if raw_content:
                extracted.append(TavilyExtractResult(url=str(item["url"]).strip(), raw_content=raw_content))
        return TavilyResponse(ok=True, status_code=200, extract_results=extracted)
