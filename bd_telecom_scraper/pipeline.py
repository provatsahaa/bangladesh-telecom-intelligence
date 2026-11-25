"""End-to-end Bangladesh Telecom News Scraper orchestrator."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from .compliance import PoliteHttpClient
from .deduplicator import Deduplicator, canonicalize_url, content_hash, title_fingerprint
from .discovery import DHAKA_TZ, SourceDiscoverer
from .extractor import extract_article_from_html
from .filtering import candidate_looks_promising, evaluate_relevance
from .models import Article, DiscoveredItem, ScrapeRunReport, SourceConfig, SourceHealth
from .sources import get_sources
from .storage import ArticleStore

logger = logging.getLogger(__name__)


class TelecomNewsScraper:
    """Standalone, extensible scraper collecting telecom news from Bangladeshi portals."""

    def __init__(
        self,
        db_path: str | Path = "data/bd_telecom_news.db",
        *,
        lookback_days: int = 14,
        relevance_threshold: float = 2.0,
        max_article_fetches_per_source: int = 40,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.store = ArticleStore(db_path)
        self.lookback_days = lookback_days
        self.relevance_threshold = relevance_threshold
        self.max_article_fetches_per_source = max_article_fetches_per_source
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        source_ids: list[str] | None = None,
        *,
        sources_override: list[SourceConfig] | None = None,
    ) -> ScrapeRunReport:
        """Execute a full scrape cycle across configured portals."""
        run_id = f"run-{datetime.now(DHAKA_TZ).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        started_at = datetime.now(DHAKA_TZ)
        cutoff = started_at - timedelta(days=self.lookback_days)

        sources = sources_override if sources_override is not None else get_sources(source_ids)

        dedup = Deduplicator()
        existing_urls, existing_hashes, existing_titles = self.store.load_dedup_index()
        dedup.seed(existing_urls, existing_hashes, existing_titles)

        health_reports: list[SourceHealth] = []
        total_discovered = 0
        total_relevant = 0
        total_saved = 0
        total_duplicates = 0

        with PoliteHttpClient(timeout_seconds=self.timeout_seconds) as client:
            discoverer = SourceDiscoverer(client)
            for source in sources:
                logger.info("Scraping source: %s (%s)", source.publisher, source.source_id)
                health = self._process_source(
                    source=source,
                    client=client,
                    discoverer=discoverer,
                    dedup=dedup,
                    cutoff=cutoff,
                    now=started_at,
                )
                health_reports.append(health)
                total_discovered += health.discovered_count
                total_relevant += health.relevant_count
                total_saved += health.saved_count
                total_duplicates += health.duplicate_count

        finished_at = datetime.now(DHAKA_TZ)
        report = ScrapeRunReport(
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            lookback_days=self.lookback_days,
            total_discovered=total_discovered,
            total_relevant=total_relevant,
            total_saved=total_saved,
            total_duplicates=total_duplicates,
            source_health=health_reports,
        )
        self.store.save_run_report(report)
        return report

    def _process_source(
        self,
        *,
        source: SourceConfig,
        client: PoliteHttpClient,
        discoverer: SourceDiscoverer,
        dedup: Deduplicator,
        cutoff: datetime,
        now: datetime,
    ) -> SourceHealth:
        health = SourceHealth(
            source_id=source.source_id,
            publisher=source.publisher,
            status="OK",
        )
        items, methods_used, errors, blocked_by_waf = discoverer.discover(
            source=source, cutoff=cutoff, now=now
        )
        health.methods_used = methods_used
        health.errors.extend(errors)

        if blocked_by_waf:
            health.status = "RESTRICTED"
            return health
        if not items and errors:
            health.status = "ERROR"
            return health

        # Deduplicate discovered URLs within the same source (prefer items that already carry headline/date)
        unique_by_url: dict[str, DiscoveredItem] = {}
        for item in items:
            c_url = canonicalize_url(item.url)
            prev = unique_by_url.get(c_url)
            if prev is None or (not prev.headline and item.headline) or (prev.published_at is None and item.published_at is not None):
                unique_by_url[c_url] = item

        candidates = list(unique_by_url.values())
        health.discovered_count = len(candidates)

        # Pre-filter by date window (when already known) and telecom URL/headline hints
        promising: list[tuple[str, DiscoveredItem]] = []
        for item in candidates:
            if item.published_at is not None and item.published_at < cutoff:
                continue
            health.in_window_count += 1
            if candidate_looks_promising(item.url, item.headline, item.summary):
                promising.append((canonicalize_url(item.url), item))

        # Prioritize newest & headline-bearing candidates
        promising.sort(
            key=lambda pair: (
                pair[1].published_at.timestamp() if pair[1].published_at else 0.0,
                1 if pair[1].headline else 0,
            ),
            reverse=True,
        )

        fetches = 0
        for c_url, item in promising:
            if fetches >= self.max_article_fetches_per_source:
                break

            # Early URL dedup check before hitting the network
            if c_url in dedup._seen_urls:
                health.duplicate_count += 1
                continue

            fetches += 1
            res = client.get(
                item.url,
                rate_limit_seconds=source.rate_limit_seconds,
                respect_robots=source.respect_robots_txt,
            )

            headline = item.headline
            summary = item.summary
            content = ""
            published_at = item.published_at

            if res.ok:
                extracted = extract_article_from_html(
                    res.text,
                    res.url,
                    fallback_headline=item.headline,
                    fallback_summary=item.summary,
                    fallback_published_at=item.published_at,
                )
                headline = extracted.headline or headline
                summary = extracted.summary or summary
                content = extracted.content
                published_at = extracted.published_at or published_at
            else:
                health.errors.append(f"Article {item.url}: {res.error}")
                # If we have no headline/summary fallback, skip
                if not headline:
                    continue

            # Enforce strict 14-day window using the authoritative article publication date
            if published_at is not None and published_at < cutoff:
                continue

            # Final bilingual telecom relevance check on full headline + summary + body
            rel = evaluate_relevance(
                headline=headline,
                summary=summary,
                content=content,
                threshold=self.relevance_threshold,
            )
            if not rel.is_relevant:
                continue

            health.relevant_count += 1
            c_hash = content_hash(headline, content or summary)
            is_dup, _reason = dedup.is_duplicate(c_url, headline, c_hash)
            if is_dup:
                health.duplicate_count += 1
                continue

            article = Article(
                url=item.url,
                canonical_url=c_url,
                headline=headline,
                publisher=source.publisher,
                source_id=source.source_id,
                language=source.language,
                published_at=published_at,
                scraped_at=datetime.now(DHAKA_TZ),
                summary=summary,
                content=content,
                entities=rel.entities,
                matched_topics=rel.matched_topics,
                matched_keywords=rel.matched_keywords,
                relevance_score=rel.score,
                discovery_method=item.discovery_method,
                content_hash=c_hash,
                title_fingerprint=title_fingerprint(headline),
            )
            if self.store.save_article(article):
                dedup.register(c_url, headline, c_hash)
                health.saved_count += 1
            else:
                health.duplicate_count += 1

        if errors and health.saved_count > 0:
            health.status = "PARTIAL"
        return health
