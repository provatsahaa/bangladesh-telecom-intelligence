"""Official Meta Graph API v27.0 client for compliant Facebook Page post collection.

Supports:
  - Pages managed by the authenticated user (`pages_read_engagement`)
  - Third-party public Pages when the app has approved Advanced Access to
    Page Public Content Access (PPCA)

Strictly honors Meta Platform Terms, rate limits (`X-App-Usage` / `X-Page-Usage`),
and v26.0/v27.0 protocol rules (explicit field declarations, no legacy `ids=`,
`date_format`, `pretty`, or `If-None-Match`). Never circumvents access controls.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from ..discovery import DHAKA_TZ, parse_datetime_flexible
from ..tavily.config import is_usable_credential

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v27.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

POST_FIELDS = (
    "id,message,created_time,permalink_url,"
    "attachments{type,media_type,url,title},"
    "shares,"
    "reactions.summary(total_count).limit(0),"
    "comments.summary(total_count).limit(0)"
)

# Meta Graph error codes indicating missing permission / PPCA / auth
_PERMISSION_ERROR_CODES = frozenset({10, 100, 190, 200, 283})
_RATE_LIMIT_ERROR_CODES = frozenset({4, 17, 32, 613})


@dataclass
class GraphPageFetchResult:
    """Result of fetching posts for one Facebook Page via Graph API."""

    status: str  # "OK" | "PERMISSION_REQUIRED" | "RATE_LIMITED" | "NO_TOKEN" | "ERROR"
    posts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    pages_fetched: int = 0


class MetaGraphClient:
    """Thin, compliant client for Meta Graph API v27.0."""

    def __init__(
        self,
        access_token: str | None = None,
        *,
        api_version: str = GRAPH_API_VERSION,
        timeout_seconds: float = 20.0,
        max_pages_per_source: int = 10,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.access_token = access_token if access_token is not None else os.getenv("META_ACCESS_TOKEN", "")
        self.base_url = f"https://graph.facebook.com/{api_version}"
        self.max_pages_per_source = max_pages_per_source
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> MetaGraphClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def has_token(self) -> bool:
        return is_usable_credential(self.access_token)

    def _check_usage_headers(self, headers: httpx.Headers) -> None:
        """Honor X-App-Usage / X-Page-Usage headers by throttling before hitting 100%."""
        for header_name in ("x-app-usage", "x-page-usage"):
            raw = headers.get(header_name)
            if not raw:
                continue
            try:
                usage = json.loads(raw)
                peak = max(
                    float(usage.get("call_count", 0)),
                    float(usage.get("total_cputime", 0)),
                    float(usage.get("total_time", 0)),
                )
                if peak >= 75.0:
                    sleep_sec = 5.0 if peak < 90.0 else 15.0
                    logger.warning(
                        "Meta Graph API %s at %.1f%%; pausing %.1fs to respect rate limits.",
                        header_name,
                        peak,
                        sleep_sec,
                    )
                    time.sleep(sleep_sec)
            except (ValueError, TypeError):
                pass

    def fetch_page_posts(
        self,
        page_identifier: str,
        *,
        since: datetime,
        until: datetime | None = None,
        endpoint: str = "published_posts",
        limit: int = 25,
    ) -> GraphPageFetchResult:
        """Fetch public posts for a Page within [since, until] using cursor pagination."""
        if not self.has_token:
            return GraphPageFetchResult(
                status="NO_TOKEN",
                error="META_ACCESS_TOKEN is not configured. Use manual ingestion or set a valid token.",
            )

        url = f"{self.base_url}/{page_identifier}/{endpoint}"
        params: dict[str, Any] = {
            "fields": POST_FIELDS,
            "since": int(since.timestamp()),
            "limit": min(max(limit, 1), 100),
            "access_token": self.access_token,
        }
        if until is not None:
            params["until"] = int(until.timestamp())

        collected: list[dict[str, Any]] = []
        pages_fetched = 0
        next_url: str | None = url
        next_params: dict[str, Any] | None = params

        while next_url and pages_fetched < self.max_pages_per_source:
            try:
                resp = self._client.get(next_url, params=next_params)
            except httpx.HTTPError as exc:
                return GraphPageFetchResult(
                    status="ERROR",
                    posts=collected,
                    error=f"Network error: {type(exc).__name__}: {exc}",
                    pages_fetched=pages_fetched,
                )

            self._check_usage_headers(resp.headers)

            if resp.status_code != 200:
                code, message = self._parse_graph_error(resp)
                if resp.status_code == 429 or code in _RATE_LIMIT_ERROR_CODES:
                    return GraphPageFetchResult(
                        status="RATE_LIMITED",
                        posts=collected,
                        error=f"Graph API rate limited (code={code}): {message}",
                        pages_fetched=pages_fetched,
                    )
                if code in _PERMISSION_ERROR_CODES or resp.status_code in (401, 403):
                    return GraphPageFetchResult(
                        status="PERMISSION_REQUIRED",
                        posts=collected,
                        error=(
                            f"Graph API access restricted (code={code}): {message}. "
                            "Requires Page management permission or approved Page Public Content Access (PPCA)."
                        ),
                        pages_fetched=pages_fetched,
                    )
                return GraphPageFetchResult(
                    status="ERROR",
                    posts=collected,
                    error=f"HTTP {resp.status_code} (code={code}): {message}",
                    pages_fetched=pages_fetched,
                )

            payload = resp.json()
            data = payload.get("data", [])
            if not isinstance(data, list):
                break
            collected.extend(data)
            pages_fetched += 1

            paging = payload.get("paging", {})
            next_url = paging.get("next") if isinstance(paging, dict) else None
            # `paging.next` already includes query params and access_token
            next_params = None

        return GraphPageFetchResult(status="OK", posts=collected, pages_fetched=pages_fetched)

    @staticmethod
    def _parse_graph_error(resp: httpx.Response) -> tuple[int | None, str]:
        try:
            err = resp.json().get("error", {})
            return err.get("code"), str(err.get("message", resp.text[:200]))
        except Exception:
            return None, resp.text[:200]


def normalize_graph_post_payload(
    raw: dict[str, Any],
    *,
    page_name: str,
    page_id: str | None,
) -> dict[str, Any]:
    """Extract permitted fields from a raw Graph API post object without fabricating missing values."""
    post_id = str(raw["id"]) if raw.get("id") else None
    post_url = str(raw.get("permalink_url") or "")
    if not post_url and post_id:
        post_url = f"https://www.facebook.com/{post_id}"

    # Extract media references (metadata/URLs only — never downloads media files)
    media_refs: list[dict[str, Any]] | None = None
    attachments = raw.get("attachments", {}).get("data", []) if isinstance(raw.get("attachments"), dict) else []
    if attachments:
        refs = []
        for att in attachments:
            if not isinstance(att, dict):
                continue
            ref: dict[str, Any] = {}
            if att.get("type"):
                ref["type"] = att["type"]
            if att.get("media_type"):
                ref["media_type"] = att["media_type"]
            if att.get("url"):
                ref["url"] = att["url"]
            if att.get("title"):
                ref["title"] = att["title"]
            if ref:
                refs.append(ref)
        if refs:
            media_refs = refs

    # Extract engagement metrics only where returned by API
    engagement: dict[str, int] = {}
    if isinstance(raw.get("reactions"), dict):
        count = raw["reactions"].get("summary", {}).get("total_count")
        if isinstance(count, int):
            engagement["reactions"] = count
    if isinstance(raw.get("comments"), dict):
        count = raw["comments"].get("summary", {}).get("total_count")
        if isinstance(count, int):
            engagement["comments"] = count
    if isinstance(raw.get("shares"), dict) and isinstance(raw["shares"].get("count"), int):
        engagement["shares"] = raw["shares"]["count"]

    return {
        "page_name": page_name,
        "page_id": page_id,
        "post_id": post_id,
        "post_url": post_url,
        "post_text": str(raw.get("message") or "").strip(),
        "published_at": parse_datetime_flexible(raw.get("created_time")),
        "media_refs": media_refs,
        "engagement": engagement if engagement else None,
        "collected_at": datetime.now(DHAKA_TZ),
    }
