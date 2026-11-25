"""Offline unit tests for the Tavily Search & Extract discovery integration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import httpx

from bd_telecom_scraper.compliance import FetchResult
from bd_telecom_scraper.discovery import DHAKA_TZ
from bd_telecom_scraper.models import Article
from bd_telecom_scraper.storage import ArticleStore
from bd_telecom_scraper.tavily.client import TavilyRestClient
from bd_telecom_scraper.tavily.config import TavilyConfig
from bd_telecom_scraper.tavily.pipeline import TavilyDiscoveryPipeline
from bd_telecom_scraper.tavily.queries import TavilyQuerySpec


class _StubHttpClient:
    """Simulates free primary HTML extraction succeeding on one domain and 403-failing on another."""

    def __init__(self) -> None:
        self.requested_urls: list[str] = []

    def get(self, url: str, *, rate_limit_seconds: float | None = None, respect_robots: bool = True) -> FetchResult:
        self.requested_urls.append(url)
        if "kalerkantho.com" in url:
            return FetchResult(url=url, status_code=403, blocked_reason="http_403_waf", error="HTTP 403")
        html = """
        <html><head><title>VEON and Banglalink expand 4G and AI services</title></head>
        <body><article>
          <h1>VEON and Banglalink expand 4G and AI services in Bangladesh</h1>
          <p>VEON and Banglalink announced a major expansion of 4G network infrastructure and AI-driven digital services across Bangladesh following discussions with BTRC.</p>
          <p>The partnership targets higher broadband speeds, 5G readiness, and improved mobile financial services for subscribers nationwide.</p>
        </article></body></html>
        """
        return FetchResult(url=url, status_code=200, text=html, content_type="text/html")


def test_tavily_discovery_dedup_extract_fallback_cooldown_and_budget(tmp_path: Path) -> None:
    db_path = tmp_path / "tavily_test.db"

    # Seed one already-collected article to verify duplicate API call / fetch suppression
    store = ArticleStore(db_path)
    store.save_article(
        Article(
            url="https://www.tbsnews.net/tech/already-collected-story",
            canonical_url="https://www.tbsnews.net/tech/already-collected-story",
            headline="Grameenphone already collected story",
            publisher="The Business Standard",
            source_id="tbsnews",
            language="en",
            published_at=datetime(2026, 11, 24, 10, 0, tzinfo=DHAKA_TZ),
            scraped_at=datetime(2026, 11, 24, 11, 0, tzinfo=DHAKA_TZ),
            summary="Already in database.",
            content="Already in database body text.",
            entities=["Grameenphone"],
            matched_topics=["operators"],
            matched_keywords=["grameenphone"],
            relevance_score=5.0,
            discovery_method="rss",
            content_hash="existing_hash_1",
            title_fingerprint="existing_fp_1",
        )
    )

    api_calls: list[str] = []

    def tavily_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        api_calls.append(path)
        if path == "/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://www.tbsnews.net/tech/already-collected-story?utm_source=tavily",
                            "title": "Grameenphone already collected story",
                            "content": "Duplicate snippet.",
                            "published_date": "2026-11-24T10:00:00Z",
                            "score": 0.95,
                        },
                        {
                            "url": "https://www.reuters.com/technology/veon-banglalink-ai-network-2026",
                            "title": "VEON and Banglalink expand 4G and AI services in Bangladesh",
                            "content": "VEON and Banglalink expand 4G network and AI services.",
                            "published_date": "2026-11-24T12:00:00Z",
                            "score": 0.92,
                        },
                        {
                            "url": "https://www.kalerkantho.com/online/business/2026/11/24/btrc-5g-spectrum",
                            "title": "BTRC announces new 5G spectrum roadmap for Grameenphone and Robi",
                            "content": "BTRC announced 5G spectrum plans.",
                            "published_date": "2026-11-24T14:00:00Z",
                            "score": 0.90,
                        },
                    ]
                },
            )
        if path == "/extract":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://www.kalerkantho.com/online/business/2026/11/24/btrc-5g-spectrum",
                            "raw_content": (
                                "BTRC announced a revised 5G spectrum roadmap covering Grameenphone, Robi, "
                                "Banglalink, and Teletalk to accelerate commercial 5G deployment and broadband "
                                "network infrastructure across Bangladesh."
                            ),
                        }
                    ]
                },
            )
        return httpx.Response(404)

    cfg = TavilyConfig(
        api_key="tvly-test-key",
        search_depth="basic",
        extract_depth="basic",
        min_interval_hours=12.0,
        monthly_credit_budget=3,
    )
    t_client = TavilyRestClient(cfg, http_client=httpx.Client(transport=httpx.MockTransport(tavily_handler)))
    h_client = _StubHttpClient()
    pipeline = TavilyDiscoveryPipeline(
        db_path=db_path,
        config=cfg,
        tavily_client=t_client,
        http_client=h_client,  # type: ignore[arg-type]
    )

    q = TavilyQuerySpec(query_id="test_bd_telecom", scope="bangladesh", query="Banglalink BTRC 5G")
    rep = pipeline.run(days=14, queries_override=[q])

    assert rep.queries_executed == 1
    assert rep.skipped_already_collected == 1
    # Duplicate URL must NEVER trigger primary HTTP fetch or Tavily Extract
    assert not any("already-collected-story" in u for u in h_client.requested_urls)
    assert rep.extracted_primary == 1
    assert rep.extracted_tavily_fallback == 1
    assert rep.articles_saved == 2
    assert rep.credits_used_this_run == 2.0  # 1 search + 1 extract (1 URL -> ceil(1/5)=1)
    assert api_calls == ["/search", "/extract"]

    # Verify saved into existing `articles` table cleanly
    all_articles = store.fetch_all_articles()
    assert len(all_articles) == 3
    methods = {a["discovery_method"] for a in all_articles}
    assert {"rss", "tavily_search", "tavily_extract"} == methods

    # Second run within 12h cooldown MUST skip the query (0 credits)
    rep2 = pipeline.run(days=14, queries_override=[q])
    assert rep2.queries_executed == 0
    assert rep2.queries_skipped_cooldown == 1
    assert rep2.credits_used_this_run == 0.0

    # Forced run exceeding remaining monthly budget (2 used of 3 budget -> 1 search ok, next blocked)
    q2 = TavilyQuerySpec(query_id="q_budget_1", scope="global", query="5G RAN")
    q3 = TavilyQuerySpec(query_id="q_budget_2", scope="global", query="VEON")
    rep3 = pipeline.run(days=14, queries_override=[q2, q3], force=True)
    assert rep3.queries_executed == 1
    assert rep3.queries_blocked_budget == 1
