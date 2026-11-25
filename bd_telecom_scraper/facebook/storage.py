"""Additive SQLite storage for Facebook Page sources, posts, sync watermarks, and unified intel view.

Shares the same SQLite database (`data/bd_telecom_news.db`) as the existing news scraper
without altering the `articles` or `scrape_runs` tables.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..discovery import DHAKA_TZ
from ..storage import SCHEMA_SQL as NEWS_SCHEMA_SQL, open_sqlite_connection
from .models import FacebookPost, FacebookSource, FacebookSyncState

FACEBOOK_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS facebook_sources (
    source_id TEXT PRIMARY KEY,
    page_name TEXT NOT NULL,
    category TEXT NOT NULL,
    official_website TEXT,
    facebook_url TEXT,
    facebook_page_id TEXT,
    verification_status TEXT NOT NULL,
    verification_evidence TEXT,
    access_mode TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facebook_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT REFERENCES facebook_sources(source_id),
    page_name TEXT NOT NULL,
    page_id TEXT,
    post_id TEXT,
    post_url TEXT NOT NULL UNIQUE,
    post_text TEXT NOT NULL,
    published_at TEXT,
    collected_at TEXT NOT NULL,
    media_refs_json TEXT,
    engagement_json TEXT,
    collection_method TEXT NOT NULL,
    language TEXT NOT NULL,
    entities_json TEXT NOT NULL,
    topics_json TEXT NOT NULL,
    relevance_score REAL NOT NULL,
    relevance_classification TEXT NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fb_posts_published ON facebook_posts(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_fb_posts_source ON facebook_posts(source_id);
CREATE INDEX IF NOT EXISTS idx_fb_posts_method ON facebook_posts(collection_method);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fb_posts_post_id ON facebook_posts(post_id) WHERE post_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS facebook_sync_state (
    source_id TEXT PRIMARY KEY REFERENCES facebook_sources(source_id),
    last_synced_at TEXT,
    last_post_published_at TEXT,
    last_status TEXT NOT NULL,
    last_error TEXT
);

CREATE VIEW IF NOT EXISTS unified_telecom_intel AS
SELECT
    'news_portal' AS channel,
    source_id,
    publisher AS source_name,
    url AS item_url,
    headline AS title,
    content AS body_text,
    published_at,
    scraped_at AS collected_at,
    language,
    entities_json,
    matched_topics_json AS topics_json,
    relevance_score,
    discovery_method AS collection_method
FROM articles
UNION ALL
SELECT
    'facebook_page' AS channel,
    COALESCE(source_id, 'manual_fb') AS source_id,
    page_name AS source_name,
    post_url AS item_url,
    substr(post_text, 1, 120) AS title,
    post_text AS body_text,
    published_at,
    collected_at,
    language,
    entities_json,
    topics_json,
    relevance_score,
    collection_method
FROM facebook_posts;
"""


class FacebookStore:
    """SQLite persistence layer for Facebook sources, posts, sync state, and exports."""

    def __init__(self, db_path: str | Path = "data/bd_telecom_news.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return open_sqlite_connection(self.db_path, timeout=20.0)

    def _init_db(self) -> None:
        with self._connect() as conn:
            # Ensure base news tables exist so `unified_telecom_intel` view is always valid
            conn.executescript(NEWS_SCHEMA_SQL)
            conn.executescript(FACEBOOK_SCHEMA_SQL)

    def upsert_sources(self, sources: list[FacebookSource]) -> int:
        """Persist or update the verified Facebook Page registry in SQLite."""
        now_iso = datetime.now(DHAKA_TZ).isoformat()
        with self._connect() as conn:
            for s in sources:
                conn.execute(
                    """
                    INSERT INTO facebook_sources (
                        source_id, page_name, category, official_website,
                        facebook_url, facebook_page_id, verification_status,
                        verification_evidence, access_mode, enabled, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id) DO UPDATE SET
                        page_name=excluded.page_name,
                        category=excluded.category,
                        official_website=excluded.official_website,
                        facebook_url=excluded.facebook_url,
                        facebook_page_id=COALESCE(excluded.facebook_page_id, facebook_sources.facebook_page_id),
                        verification_status=excluded.verification_status,
                        verification_evidence=excluded.verification_evidence,
                        access_mode=excluded.access_mode,
                        enabled=excluded.enabled,
                        updated_at=excluded.updated_at
                    """,
                    (
                        s.source_id,
                        s.page_name,
                        s.category,
                        s.official_website,
                        s.facebook_url,
                        s.facebook_page_id,
                        s.verification_status,
                        s.verification_evidence,
                        s.access_mode,
                        1 if s.enabled else 0,
                        now_iso,
                    ),
                )
        return len(sources)

    def get_sync_state(self, source_id: str) -> FacebookSyncState | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM facebook_sync_state WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            return None
        return FacebookSyncState(
            source_id=row["source_id"],
            last_synced_at=datetime.fromisoformat(row["last_synced_at"]) if row["last_synced_at"] else None,
            last_post_published_at=(
                datetime.fromisoformat(row["last_post_published_at"]) if row["last_post_published_at"] else None
            ),
            last_status=row["last_status"],
            last_error=row["last_error"],
        )

    def save_sync_state(self, state: FacebookSyncState) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO facebook_sync_state (
                    source_id, last_synced_at, last_post_published_at, last_status, last_error
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    last_synced_at=excluded.last_synced_at,
                    last_post_published_at=COALESCE(excluded.last_post_published_at, facebook_sync_state.last_post_published_at),
                    last_status=excluded.last_status,
                    last_error=excluded.last_error
                """,
                (
                    state.source_id,
                    state.last_synced_at.isoformat() if state.last_synced_at else None,
                    state.last_post_published_at.isoformat() if state.last_post_published_at else None,
                    state.last_status,
                    state.last_error,
                ),
            )

    def load_dedup_index(self) -> tuple[set[str], set[str], set[str]]:
        """Return existing (post_urls, post_ids, content_hashes)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT post_url, post_id, content_hash FROM facebook_posts"
            ).fetchall()
        urls = {r["post_url"] for r in rows}
        post_ids = {r["post_id"] for r in rows if r["post_id"]}
        hashes = {r["content_hash"] for r in rows if r["content_hash"]}
        return urls, post_ids, hashes

    def save_post(self, post: FacebookPost) -> bool:
        """Insert a normalized Facebook post; returns True if inserted, False if duplicate."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO facebook_posts (
                    source_id, page_name, page_id, post_id, post_url, post_text,
                    published_at, collected_at, media_refs_json, engagement_json,
                    collection_method, language, entities_json, topics_json,
                    relevance_score, relevance_classification, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post.source_id,
                    post.page_name,
                    post.page_id,
                    post.post_id,
                    post.post_url,
                    post.post_text,
                    post.published_at.isoformat() if post.published_at else None,
                    post.collected_at.isoformat(),
                    json.dumps(post.media_refs, ensure_ascii=False) if post.media_refs is not None else None,
                    json.dumps(post.engagement, ensure_ascii=False) if post.engagement is not None else None,
                    post.collection_method,
                    post.language,
                    json.dumps(post.entities, ensure_ascii=False),
                    json.dumps(post.topics, ensure_ascii=False),
                    post.relevance_score,
                    post.relevance_classification,
                    post.content_hash,
                ),
            )
            return cur.rowcount > 0

    def fetch_all_posts(self) -> list[dict[str, Any]]:
        """Retrieve all stored Facebook posts ordered newest-first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM facebook_posts ORDER BY COALESCE(published_at, collected_at) DESC"
            ).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            result.append({
                "id": r["id"],
                "source_id": r["source_id"],
                "page_name": r["page_name"],
                "page_id": r["page_id"],
                "post_id": r["post_id"],
                "post_url": r["post_url"],
                "post_text": r["post_text"],
                "published_at": r["published_at"],
                "collected_at": r["collected_at"],
                "media_refs": json.loads(r["media_refs_json"]) if r["media_refs_json"] else None,
                "engagement": json.loads(r["engagement_json"]) if r["engagement_json"] else None,
                "collection_method": r["collection_method"],
                "language": r["language"],
                "entities": json.loads(r["entities_json"]),
                "topics": json.loads(r["topics_json"]),
                "relevance_score": r["relevance_score"],
                "relevance_classification": r["relevance_classification"],
            })
        return result

    def fetch_unified_intel(self, limit: int = 200) -> list[dict[str, Any]]:
        """Query the unified_telecom_intel view combining news portals + Facebook pages."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM unified_telecom_intel ORDER BY COALESCE(published_at, collected_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def export_json(self, output_path: str | Path) -> int:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        posts = self.fetch_all_posts()
        path.write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(posts)

    def export_csv(self, output_path: str | Path) -> int:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        posts = self.fetch_all_posts()
        fieldnames = [
            "id", "published_at", "page_name", "page_id", "post_id", "post_url",
            "collection_method", "language", "relevance_classification", "relevance_score",
            "entities", "topics", "engagement", "post_text", "collected_at",
        ]
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for p in posts:
                writer.writerow({
                    "id": p["id"],
                    "published_at": p["published_at"] or "",
                    "page_name": p["page_name"],
                    "page_id": p["page_id"] or "",
                    "post_id": p["post_id"] or "",
                    "post_url": p["post_url"],
                    "collection_method": p["collection_method"],
                    "language": p["language"],
                    "relevance_classification": p["relevance_classification"],
                    "relevance_score": p["relevance_score"],
                    "entities": "; ".join(p["entities"]),
                    "topics": "; ".join(p["topics"]),
                    "engagement": json.dumps(p["engagement"]) if p["engagement"] else "",
                    "post_text": p["post_text"],
                    "collected_at": p["collected_at"],
                })
        return len(posts)

    def purge_source(self, source_id: str) -> int:
        """Delete all stored Facebook posts for a source_id (Meta data-deletion compliance)."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM facebook_posts WHERE source_id = ?", (source_id,))
            conn.execute("DELETE FROM facebook_sync_state WHERE source_id = ?", (source_id,))
            return cur.rowcount

    def purge_older_than(self, days: int) -> int:
        """Delete stored Facebook posts older than N days (data-retention compliance)."""
        cutoff = (datetime.now(DHAKA_TZ) - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM facebook_posts WHERE COALESCE(published_at, collected_at) < ?",
                (cutoff,),
            )
            return cur.rowcount
