"""Tavily discovery & fallback extraction pipeline integrated into existing `articles` storage.

Flow:
  1. Enforce per-query cooldown & monthly credit budget.
  2. Run Tavily Search (`POST /search`) with credible domain filtering.
  3. Immediately skip URLs/titles already in `articles` (prevents duplicate API calls).
  4. Try existing free compliant HTML extractor (`PoliteHttpClient` + `extract_article_from_html`).
  5. Batch only failed/short extractions into Tavily Extract (`POST /extract`) fallback.
  6. Run existing bilingual relevance filter, entity extractor, deduplicator, and `ArticleStore`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from ..compliance import PoliteHttpClient
from ..deduplicator import Deduplicator, canonicalize_url, content_hash, title_fingerprint
from ..discovery import DHAKA_TZ, parse_datetime_flexible
from ..extractor import extract_article_from_html
from ..filtering import evaluate_relevance
from ..models import Article
from ..storage import ArticleStore
from .client import TavilyRestClient, TavilySearchHit
from .config import TavilyConfig, load_tavily_config
from .queries import TavilyQuerySpec, get_tavily_queries
from .usage import TavilyUsageTracker, estimate_extract_credits

logger = logging.getLogger(__name__)

_DOMAIN_PUBLISHER_MAP: dict[str, str] = {
    "thedailystar.net": "The Daily Star",
    "tbsnews.net": "The Business Standard",
    "prothomalo.com": "Prothom Alo",
    "dhakatribune.com": "Dhaka Tribune",
    "thefinancialexpress.com.bd": "The Financial Express",
    "newagebd.net": "New Age",
    "bdnews24.com": "bdnews24",
    "bonikbarta.com": "Bonik Barta",
    "kalerkantho.com": "Kaler Kantho",
    "samakal.com": "Samakal",
    "jugantor.com": "Jugantor",
    "ittefaq.com.bd": "The Daily Ittefaq",
    "banglatribune.com": "Bangla Tribune",
    "techshohor.com": "Techshohor",
    "bssnews.net": "BSS News",
    "daily-sun.com": "Daily Sun",
    "unb.com.bd": "UNB",
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "lightreading.com": "Light Reading",
    "rcrwireless.com": "RCR Wireless News",
    "telecomtv.com": "TelecomTV",
    "capacitymedia.com": "Capacity Media",
    "developingtelecoms.com": "Developing Telecoms",
    "mobileworldlive.com": "Mobile World Live",
    "fierce-network.com": "Fierce Network",
    "gsma.com": "GSMA",
    "telecomreview.com": "Telecom Review",
    "veon.com": "VEON",
}

_BANGLA_RE = re.compile(r"[\u0980-\u09FF]")


def _resolve_publisher(url: str) -> tuple[str, str]:
    """Derive clean (source_id, publisher_name) from an article URL domain."""
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    for domain, name in _DOMAIN_PUBLISHER_MAP.items():
        if host == domain or host.endswith("." + domain):
            source_id = "tavily_" + domain.split(".")[0].replace("-", "_")
            return source_id, name
    base = host.split(".")[0] if host else "unknown"
    return f"tavily_{base}", host or "Unknown Publisher"


def _detect_lang(text: str) -> str:
    return "bn" if len(_BANGLA_RE.findall(text or "")) >= 15 else "en"


@dataclass
class TavilyRunReport:
    """Summary of a Tavily discovery & fallback extraction run."""

    queries_executed: int = 0
    queries_skipped_cooldown: int = 0
    queries_blocked_budget: int = 0
    hits_returned: int = 0
    skipped_already_collected: int = 0
    extracted_primary: int = 0
    extracted_tavily_fallback: int = 0
    articles_saved: int = 0
    credits_used_this_run: float = 0.0
    monthly_credits_used: float = 0.0
    monthly_budget: int = 800
    errors: list[str] = field(default_factory=list)


class TavilyDiscoveryPipeline:
    """Additive Tavily Search & Extract discovery pipeline sharing `data/bd_telecom_news.db`."""

    def __init__(
        self,
        db_path: str | Path = "data/bd_telecom_news.db",
        *,
        config: TavilyConfig | None = None,
        tavily_client: TavilyRestClient | None = None,
        http_client: PoliteHttpClient | None = None,
        relevance_threshold: float = 2.0,
    ) -> None:
        self.db_path = Path(db_path)
        self.config = config or load_tavily_config()
        self.store = ArticleStore(self.db_path)
        self.usage = TavilyUsageTracker(self.db_path)
        self.relevance_threshold = relevance_threshold
        self._tavily_client = tavily_client
        self._http_client = http_client

    def run(
        self,
        *,
        days: int = 14,
        query_ids: list[str] | None = None,
        scope: str | None = None,
        max_queries: int | None = None,
        force: bool = False,
        queries_override: list[TavilyQuerySpec] | None = None,
    ) -> TavilyRunReport:
        """Execute Tavily discovery, free primary extraction, Tavily Extract fallback, and storage."""
        report = TavilyRunReport(monthly_budget=self.config.monthly_credit_budget)
        queries = queries_override if queries_override is not None else get_tavily_queries(query_ids, scope=scope)
        if max_queries is not None:
            queries = queries[:max_queries]

        dedup = Deduplicator()
        existing_urls, existing_hashes, existing_titles = self.store.load_dedup_index()
        dedup.seed(existing_urls, existing_hashes, existing_titles)

        cutoff = datetime.now(DHAKA_TZ) - timedelta(days=days)

        t_client = self._tavily_client or TavilyRestClient(self.config)
        owns_tavily = self._tavily_client is None
        h_client = self._http_client or PoliteHttpClient(timeout_seconds=15.0)
        owns_http = self._http_client is None

        # Track candidates needing Tavily Extract fallback across all queries in this run
        fallback_candidates: dict[str, TavilySearchHit] = {}

        try:
            for qspec in queries:
                if not force and not self.usage.is_query_due(qspec.query_id, self.config.min_interval_hours):
                    report.queries_skipped_cooldown += 1
                    continue

                cost = float(self.config.search_credit_cost)
                if not self.usage.can_spend(cost, self.config.monthly_credit_budget):
                    report.queries_blocked_budget += 1
                    self.usage.record_call(
                        endpoint="search",
                        query_id=qspec.query_id,
                        credits_used=0.0,
                        status="BUDGET_BLOCKED",
                        error_message="Monthly Tavily credit budget reached.",
                    )
                    report.errors.append(f"Budget cap reached before query '{qspec.query_id}'.")
                    break

                resp = t_client.search(
                    query=qspec.query,
                    days=days,
                    include_domains=qspec.include_domains,
                    exclude_domains=qspec.exclude_domains,
                )
                report.queries_executed += 1

                if not resp.ok:
                    self.usage.record_call(
                        endpoint="search",
                        query_id=qspec.query_id,
                        credits_used=0.0,
                        status="ERROR",
                        error_message=resp.error,
                    )
                    report.errors.append(f"Search '{qspec.query_id}' failed: {resp.error}")
                    continue

                report.credits_used_this_run += cost
                report.hits_returned += len(resp.search_hits)
                saved_for_query = 0

                for hit in resp.search_hits:
                    c_url = canonicalize_url(hit.url)
                    # Prevent duplicate API calls & fetches for already-collected articles
                    is_dup, _ = dedup.is_duplicate(c_url, hit.title, "")
                    if is_dup:
                        report.skipped_already_collected += 1
                        continue

                    # Try existing free compliant extractor first
                    fetch_res = h_client.get(hit.url, rate_limit_seconds=1.2, respect_robots=True)
                    extracted_ok = False
                    if fetch_res.ok:
                        ext = extract_article_from_html(
                            fetch_res.text,
                            fetch_res.url,
                            fallback_headline=hit.title,
                            fallback_summary=hit.content[:320],
                            fallback_published_at=parse_datetime_flexible(hit.published_date),
                        )
                        if len(ext.content) >= 120:
                            extracted_ok = True
                            report.extracted_primary += 1
                            if self._filter_and_save(
                                url=hit.url,
                                canonical_url=c_url,
                                headline=ext.headline or hit.title,
                                summary=ext.summary or hit.content[:320],
                                content=ext.content,
                                published_at=ext.published_at or parse_datetime_flexible(hit.published_date),
                                cutoff=cutoff,
                                discovery_method="tavily_search",
                                dedup=dedup,
                            ):
                                saved_for_query += 1
                                report.articles_saved += 1

                    if not extracted_ok and c_url not in fallback_candidates:
                        fallback_candidates[c_url] = hit

                self.usage.record_call(
                    endpoint="search",
                    query_id=qspec.query_id,
                    credits_used=cost,
                    results_returned=len(resp.search_hits),
                    new_articles_saved=saved_for_query,
                    status="OK",
                )
                self.usage.mark_query_ran(qspec.query_id, len(resp.search_hits), saved_for_query)

            # Step 2: Batched Tavily Extract fallback ONLY for URLs where primary HTML extraction failed
            if self.config.enable_extract_fallback and fallback_candidates:
                pending_items = list(fallback_candidates.items())
                for i in range(0, len(pending_items), 5):
                    batch = pending_items[i : i + 5]
                    est_cost = estimate_extract_credits(len(batch), self.config.extract_depth)
                    if not self.usage.can_spend(est_cost, self.config.monthly_credit_budget):
                        self.usage.record_call(
                            endpoint="extract",
                            item_count=len(batch),
                            credits_used=0.0,
                            status="BUDGET_BLOCKED",
                            error_message="Monthly Tavily credit budget reached before extract fallback.",
                        )
                        break

                    urls = [hit.url for _, hit in batch]
                    ext_resp = t_client.extract(urls)
                    if not ext_resp.ok:
                        self.usage.record_call(
                            endpoint="extract",
                            item_count=len(batch),
                            credits_used=0.0,
                            status="ERROR",
                            error_message=ext_resp.error,
                        )
                        report.errors.append(f"Extract fallback failed: {ext_resp.error}")
                        continue

                    actual_credits = estimate_extract_credits(len(ext_resp.extract_results), self.config.extract_depth)
                    report.credits_used_this_run += actual_credits
                    extracted_by_url = {canonicalize_url(r.url): r.raw_content for r in ext_resp.extract_results}
                    saved_batch = 0

                    for c_url, hit in batch:
                        body = extracted_by_url.get(c_url) or hit.content
                        if not body or len(body) < 80:
                            continue
                        if c_url in extracted_by_url:
                            report.extracted_tavily_fallback += 1
                        if self._filter_and_save(
                            url=hit.url,
                            canonical_url=c_url,
                            headline=hit.title,
                            summary=hit.content[:320] if hit.content else body[:320],
                            content=body,
                            published_at=parse_datetime_flexible(hit.published_date),
                            cutoff=cutoff,
                            discovery_method="tavily_extract" if c_url in extracted_by_url else "tavily_search",
                            dedup=dedup,
                        ):
                            saved_batch += 1
                            report.articles_saved += 1

                    self.usage.record_call(
                        endpoint="extract",
                        item_count=len(batch),
                        credits_used=actual_credits,
                        results_returned=len(ext_resp.extract_results),
                        new_articles_saved=saved_batch,
                        status="OK",
                    )
        finally:
            if owns_tavily:
                t_client.close()
            if owns_http:
                h_client.close()

        report.monthly_credits_used = self.usage.get_monthly_credits_used()
        return report

    def _filter_and_save(
        self,
        *,
        url: str,
        canonical_url: str,
        headline: str,
        summary: str,
        content: str,
        published_at: datetime | None,
        cutoff: datetime,
        discovery_method: str,
        dedup: Deduplicator,
    ) -> bool:
        if published_at is not None and published_at < cutoff:
            return False

        rel = evaluate_relevance(
            headline=headline,
            summary=summary,
            content=content,
            threshold=self.relevance_threshold,
        )
        if not rel.is_relevant:
            return False

        c_hash = content_hash(headline, content or summary)
        is_dup, _ = dedup.is_duplicate(canonical_url, headline, c_hash)
        if is_dup:
            return False

        source_id, publisher = _resolve_publisher(url)
        article = Article(
            url=url,
            canonical_url=canonical_url,
            headline=headline,
            publisher=publisher,
            source_id=source_id,
            language=_detect_lang(f"{headline} {content[:400]}"),
            published_at=published_at,
            scraped_at=datetime.now(DHAKA_TZ),
            summary=summary or content[:320],
            content=content,
            entities=rel.entities,
            matched_topics=rel.matched_topics,
            matched_keywords=rel.matched_keywords,
            relevance_score=rel.score,
            discovery_method=discovery_method,
            content_hash=c_hash,
            title_fingerprint=title_fingerprint(headline),
        )
        if self.store.save_article(article):
            dedup.register(canonical_url, headline, c_hash)
            return True
        return False
