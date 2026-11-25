"""Orchestrator for compliant Facebook Page telecom intelligence collection.

Supports both:
  - Method 1: Official Meta Graph API v27.0 (managed Pages + PPCA-approved Pages)
  - Method 2: Manual Public Content Ingestion (single post or batch JSON/CSV)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from ..deduplicator import canonicalize_url, content_hash
from ..discovery import DHAKA_TZ
from ..filtering import evaluate_relevance
from .graph_client import MetaGraphClient, normalize_graph_post_payload
from .manual_ingest import (
    ManualPostSubmission,
    build_manual_submission,
    detect_language,
    load_manual_batch_file,
)
from .models import FacebookPost, FacebookRunReport, FacebookSource, FacebookSyncState
from .source_registry import DEFAULT_FACEBOOK_SOURCES, get_facebook_sources
from .storage import FacebookStore

logger = logging.getLogger(__name__)


def classify_relevance(score: float) -> str:
    """Classify relevance score into high / medium / low tiers."""
    if score >= 5.0:
        return "high"
    if score >= 2.0:
        return "medium"
    return "low"


class FacebookIntelPipeline:
    """Integrates compliant Facebook Page collection into the Bangladesh Telecom News Aggregator."""

    def __init__(
        self,
        db_path: str | Path = "data/bd_telecom_news.db",
        *,
        lookback_days: int = 14,
        relevance_threshold: float = 2.0,
    ) -> None:
        self.store = FacebookStore(db_path)
        self.lookback_days = lookback_days
        self.relevance_threshold = relevance_threshold
        self.store.upsert_sources(list(DEFAULT_FACEBOOK_SOURCES))

    def sync_via_graph_api(
        self,
        *,
        source_ids: list[str] | None = None,
        sources_override: list[FacebookSource] | None = None,
        access_token: str | None = None,
        graph_client: MetaGraphClient | None = None,
        include_manual_only: bool = False,
    ) -> FacebookRunReport:
        """Collect posts via official Meta Graph API v27.0 with 14-day initial & incremental sync."""
        started_at = datetime.now(DHAKA_TZ)
        run_id = f"fb-sync-{started_at.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        sources = sources_override if sources_override is not None else get_facebook_sources(source_ids)

        seen_urls, seen_post_ids, seen_hashes = self.store.load_dedup_index()
        report = FacebookRunReport(
            run_id=run_id,
            mode="graph_api_sync",
            started_at=started_at,
            finished_at=started_at,
        )

        client = graph_client or MetaGraphClient(access_token=access_token)
        owns_client = graph_client is None
        try:
            for src in sources:
                report.sources_checked += 1
                if src.access_mode == "manual_only" and not include_manual_only:
                    report.source_statuses[src.source_id] = "MANUAL_ONLY"
                    self.store.save_sync_state(
                        FacebookSyncState(
                            source_id=src.source_id,
                            last_synced_at=started_at,
                            last_status="MANUAL_ONLY",
                            last_error="Configured as manual_only (no PPCA / Page management access).",
                        )
                    )
                    continue

                if not src.facebook_page_id and not src.facebook_url:
                    report.source_statuses[src.source_id] = "UNVERIFIED_NO_URL"
                    continue

                page_identifier = src.facebook_page_id or src.facebook_url.rstrip("/").split("/")[-1]
                prev_state = self.store.get_sync_state(src.source_id)
                if prev_state and prev_state.last_post_published_at:
                    since = prev_state.last_post_published_at
                else:
                    since = started_at - timedelta(days=self.lookback_days)

                endpoint = "feed" if src.access_mode == "managed_api" else "published_posts"
                collection_method = (
                    "graph_api_managed" if src.access_mode == "managed_api" else "graph_api_ppca"
                )

                res = client.fetch_page_posts(
                    page_identifier,
                    since=since,
                    until=started_at,
                    endpoint=endpoint,
                )
                report.source_statuses[src.source_id] = res.status
                if res.error:
                    report.errors.append(f"{src.source_id}: {res.error}")

                newest_pub: datetime | None = prev_state.last_post_published_at if prev_state else None

                for raw in res.posts:
                    report.posts_fetched += 1
                    norm = normalize_graph_post_payload(
                        raw, page_name=src.page_name, page_id=src.facebook_page_id
                    )
                    post_text = norm["post_text"]
                    if not post_text:
                        continue
                    pub_dt: datetime | None = norm["published_at"]
                    if pub_dt and (newest_pub is None or pub_dt > newest_pub):
                        newest_pub = pub_dt

                    post = self._build_filtered_post(
                        source_id=src.source_id,
                        page_name=norm["page_name"],
                        page_id=norm["page_id"],
                        post_id=norm["post_id"],
                        post_url=norm["post_url"],
                        post_text=post_text,
                        published_at=pub_dt,
                        collected_at=norm["collected_at"],
                        media_refs=norm["media_refs"],
                        engagement=norm["engagement"],
                        collection_method=collection_method,
                    )
                    if post is None:
                        continue
                    report.posts_relevant += 1
                    if self._save_if_unique(post, seen_urls, seen_post_ids, seen_hashes):
                        report.posts_saved += 1
                    else:
                        report.posts_duplicate += 1

                self.store.save_sync_state(
                    FacebookSyncState(
                        source_id=src.source_id,
                        last_synced_at=datetime.now(DHAKA_TZ),
                        last_post_published_at=newest_pub,
                        last_status=res.status,
                        last_error=res.error,
                    )
                )
        finally:
            if owns_client:
                client.close()

        report.finished_at = datetime.now(DHAKA_TZ)
        return report

    def ingest_manual_post(
        self,
        *,
        post_url: str,
        post_text: str,
        page_name: str | None = None,
        published_at: str | datetime | None = None,
        source_id: str | None = None,
        post_id: str | None = None,
        require_relevant: bool = True,
    ) -> tuple[bool, FacebookPost | None, str]:
        """Manually ingest a single public Facebook post URL + pasted text."""
        sub = build_manual_submission(
            post_url=post_url,
            post_text=post_text,
            page_name=page_name,
            published_at=published_at,
            source_id=source_id,
            post_id=post_id,
        )
        return self._process_manual_submission(sub, require_relevant=require_relevant)

    def ingest_manual_batch(
        self,
        batch_path: str | Path,
        *,
        require_relevant: bool = True,
    ) -> FacebookRunReport:
        """Manually ingest a batch of public Facebook posts from a JSON or CSV file."""
        started_at = datetime.now(DHAKA_TZ)
        run_id = f"fb-manual-{started_at.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        submissions = load_manual_batch_file(batch_path)

        report = FacebookRunReport(
            run_id=run_id,
            mode="manual_ingest",
            started_at=started_at,
            finished_at=started_at,
            sources_checked=len({s.source_id or s.page_name for s in submissions}),
        )
        for sub in submissions:
            report.posts_fetched += 1
            saved, post, reason = self._process_manual_submission(
                sub, require_relevant=require_relevant
            )
            if post is not None:
                report.posts_relevant += 1
            if saved:
                report.posts_saved += 1
            elif reason == "duplicate":
                report.posts_duplicate += 1

        report.finished_at = datetime.now(DHAKA_TZ)
        return report

    def _process_manual_submission(
        self,
        sub: ManualPostSubmission,
        *,
        require_relevant: bool,
    ) -> tuple[bool, FacebookPost | None, str]:
        seen_urls, seen_post_ids, seen_hashes = self.store.load_dedup_index()
        post = self._build_filtered_post(
            source_id=sub.source_id,
            page_name=sub.page_name,
            page_id=sub.page_id,
            post_id=sub.post_id,
            post_url=sub.post_url,
            post_text=sub.post_text,
            published_at=sub.published_at,
            collected_at=datetime.now(DHAKA_TZ),
            media_refs=sub.media_refs,
            engagement=None,
            collection_method="manual_submission",
            require_relevant=require_relevant,
        )
        if post is None:
            return False, None, "below_relevance_threshold"
        if self._save_if_unique(post, seen_urls, seen_post_ids, seen_hashes):
            return True, post, "saved"
        return False, post, "duplicate"

    def _build_filtered_post(
        self,
        *,
        source_id: str | None,
        page_name: str,
        page_id: str | None,
        post_id: str | None,
        post_url: str,
        post_text: str,
        published_at: datetime | None,
        collected_at: datetime,
        media_refs: list[dict[str, object]] | None,
        engagement: dict[str, int] | None,
        collection_method: str,
        require_relevant: bool = True,
    ) -> FacebookPost | None:
        # Use first line / 140 chars as headline-equivalent for relevance scoring
        first_line = post_text.splitlines()[0][:140] if post_text else ""
        rel = evaluate_relevance(
            headline=first_line,
            summary="",
            content=post_text,
            threshold=self.relevance_threshold,
        )
        if require_relevant and not rel.is_relevant:
            return None

        c_url = canonicalize_url(post_url)
        c_hash = content_hash(first_line, post_text)
        return FacebookPost(
            source_id=source_id,
            page_name=page_name,
            page_id=page_id,
            post_id=post_id,
            post_url=c_url,
            post_text=post_text,
            published_at=published_at,
            collected_at=collected_at,
            media_refs=media_refs,
            engagement=engagement,
            collection_method=collection_method,
            language=detect_language(post_text),
            entities=rel.entities,
            topics=rel.matched_topics,
            relevance_score=rel.score,
            relevance_classification=classify_relevance(rel.score),
            content_hash=c_hash,
        )

    def _save_if_unique(
        self,
        post: FacebookPost,
        seen_urls: set[str],
        seen_post_ids: set[str],
        seen_hashes: set[str],
    ) -> bool:
        if post.post_url in seen_urls:
            return False
        if post.post_id and post.post_id in seen_post_ids:
            return False
        if post.content_hash in seen_hashes:
            return False
        if self.store.save_post(post):
            seen_urls.add(post.post_url)
            if post.post_id:
                seen_post_ids.add(post.post_id)
            seen_hashes.add(post.content_hash)
            return True
        return False
