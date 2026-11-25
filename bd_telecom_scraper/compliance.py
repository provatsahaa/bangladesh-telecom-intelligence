"""HTTP compliance layer: robots.txt enforcement, polite rate limiting, and safe fetching.

Strictly respects robots.txt directives and never attempts to bypass HTTP 403
anti-bot walls, Cloudflare challenges, or paywalls.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "BDTelecomNewsAggregator/1.0 (+https://example.local/bd-telecom-aggregator; research-bot)"
)


@dataclass
class FetchResult:
    """Outcome of a compliant HTTP GET request."""

    url: str
    status_code: int | None
    text: str = ""
    content_type: str = ""
    blocked_reason: str | None = None  # "robots_disallow", "http_403_waf", "http_429", "network_error"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and self.blocked_reason is None


class PoliteHttpClient:
    """Synchronous compliant HTTP client with per-domain rate limiting and robots.txt cache."""

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_seconds: float = 15.0,
        default_rate_limit: float = 1.5,
        max_retries: int = 1,
    ) -> None:
        self.user_agent = user_agent
        self.default_rate_limit = default_rate_limit
        self.max_retries = max_retries
        self._client = httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,bn;q=0.8",
            },
            timeout=timeout_seconds,
            follow_redirects=True,
        )
        self._last_request_by_domain: dict[str, float] = {}
        self._robots_cache: dict[str, RobotFileParser | None] = {}
        self._robots_blocked_domains: set[str] = set()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteHttpClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _wait_for_rate_limit(self, domain: str, min_interval: float) -> None:
        now = time.monotonic()
        last = self._last_request_by_domain.get(domain)
        if last is not None:
            elapsed = now - last
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
        self._last_request_by_domain[domain] = time.monotonic()

    def _get_robots_parser(self, base_url: str) -> RobotFileParser | None:
        parsed = urlparse(base_url)
        domain = parsed.netloc.lower()
        if domain in self._robots_cache:
            return self._robots_cache[domain]

        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        self._wait_for_rate_limit(domain, min_interval=0.5)
        try:
            resp = self._client.get(robots_url)
            if resp.status_code == 200:
                rp = RobotFileParser()
                rp.set_url(robots_url)
                rp.parse(resp.text.splitlines())
                self._robots_cache[domain] = rp
                return rp
            if resp.status_code in (401, 403):
                # Conservative: if even robots.txt returns 403 (e.g. Cloudflare WAF),
                # mark domain as restricted rather than probing further.
                self._robots_blocked_domains.add(domain)
                self._robots_cache[domain] = None
                return None
            # 404 or 5xx on robots.txt -> standard RFC 9309 allows cautious crawling
            self._robots_cache[domain] = None
            return None
        except httpx.HTTPError as exc:
            logger.debug("robots.txt fetch failed for %s: %s", domain, exc)
            self._robots_cache[domain] = None
            return None

    def is_allowed(self, url: str, respect_robots: bool = True) -> tuple[bool, str | None]:
        """Check whether fetching `url` is permitted by robots.txt."""
        if not respect_robots:
            return True, None
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        rp = self._get_robots_parser(url)
        if domain in self._robots_blocked_domains:
            return False, "http_403_waf"
        if rp is None:
            return True, None
        # Check our declared bot user-agent (which falls back to '*' in RobotFileParser)
        if not rp.can_fetch(self.user_agent, url):
            return False, "robots_disallow"
        return True, None

    def get(
        self,
        url: str,
        *,
        rate_limit_seconds: float | None = None,
        respect_robots: bool = True,
    ) -> FetchResult:
        """Perform a compliant GET request honoring robots.txt and rate limits."""
        allowed, reason = self.is_allowed(url, respect_robots=respect_robots)
        if not allowed:
            return FetchResult(
                url=url,
                status_code=403 if reason == "http_403_waf" else None,
                blocked_reason=reason,
                error=f"Blocked before fetch ({reason})",
            )

        domain = urlparse(url).netloc.lower()
        interval = rate_limit_seconds if rate_limit_seconds is not None else self.default_rate_limit

        for attempt in range(self.max_retries + 1):
            self._wait_for_rate_limit(domain, min_interval=interval)
            try:
                resp = self._client.get(url)
                ct = resp.headers.get("content-type", "")
                if resp.status_code == 200:
                    return FetchResult(
                        url=str(resp.url),
                        status_code=200,
                        text=resp.text,
                        content_type=ct,
                    )
                if resp.status_code == 403:
                    return FetchResult(
                        url=url,
                        status_code=403,
                        content_type=ct,
                        blocked_reason="http_403_waf",
                        error="HTTP 403 Forbidden (likely WAF/Cloudflare); not bypassed.",
                    )
                if resp.status_code == 429:
                    if attempt < self.max_retries:
                        retry_after = min(float(resp.headers.get("Retry-After", "5")), 15.0)
                        time.sleep(retry_after)
                        continue
                    return FetchResult(
                        url=url,
                        status_code=429,
                        content_type=ct,
                        blocked_reason="http_429",
                        error="HTTP 429 Too Many Requests",
                    )
                if resp.status_code >= 500 and attempt < self.max_retries:
                    time.sleep(2.0)
                    continue
                return FetchResult(
                    url=url,
                    status_code=resp.status_code,
                    content_type=ct,
                    error=f"HTTP {resp.status_code}",
                )
            except httpx.HTTPError as exc:
                if attempt < self.max_retries:
                    time.sleep(1.5)
                    continue
                return FetchResult(
                    url=url,
                    status_code=None,
                    blocked_reason="network_error",
                    error=f"{type(exc).__name__}: {exc}",
                )

        return FetchResult(url=url, status_code=None, blocked_reason="network_error", error="Exhausted retries")
