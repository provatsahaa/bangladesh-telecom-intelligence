"""Structured storage layer: SQLite primary database + JSON and CSV exporters.

Schema is intentionally clean and extensible so future intelligence modules
(categorization, sentiment analysis, trend detection, operator comparison)
can join on `articles.id` without migrating or breaking the scraping layer.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from .models import Article, ScrapeRunReport

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_url TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    headline TEXT NOT NULL,
    publisher TEXT NOT NULL,
    source_id TEXT NOT NULL,
    language TEXT NOT NULL,
    published_at TEXT,
    scraped_at TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    entities_json TEXT NOT NULL,
    matched_topics_json TEXT NOT NULL,
    matched_keywords_json TEXT NOT NULL,
    relevance_score REAL NOT NULL,
    discovery_method TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    title_fingerprint TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_source_id ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_articles_title_fp ON articles(title_fingerprint);

CREATE TABLE IF NOT EXISTS scrape_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    lookback_days INTEGER NOT NULL,
    total_discovered INTEGER NOT NULL,
    total_relevant INTEGER NOT NULL,
    total_saved INTEGER NOT NULL,
    total_duplicates INTEGER NOT NULL,
    report_json TEXT NOT NULL
);
"""


def open_sqlite_connection(
    db_path: str | Path,
    *,
    timeout: float = 20.0,
    enable_wal: bool = True,
) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys, busy timeout, and WAL mode configured."""
    conn = sqlite3.connect(db_path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)};")
    if enable_wal:
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
        except sqlite3.OperationalError:
            # Read-only or in-memory DBs may not support WAL; fall back gracefully
            pass
    return conn


class ArticleStore:
    """SQLite-backed store for telecom news articles with JSON/CSV export support."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return open_sqlite_connection(self.db_path, timeout=20.0)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

    def load_dedup_index(self) -> tuple[set[str], set[str], list[str]]:
        """Return existing (canonical_urls, content_hashes, headlines) for deduplication."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT canonical_url, content_hash, headline FROM articles"
            ).fetchall()
        urls = {r["canonical_url"] for r in rows}
        hashes = {r["content_hash"] for r in rows if r["content_hash"]}
        titles = [r["headline"] for r in rows if r["headline"]]
        return urls, hashes, titles

    def save_article(self, article: Article) -> bool:
        """Insert an article; returns True if inserted, False if canonical_url already exists."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO articles (
                    canonical_url, url, headline, publisher, source_id, language,
                    published_at, scraped_at, summary, content,
                    entities_json, matched_topics_json, matched_keywords_json,
                    relevance_score, discovery_method, content_hash, title_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article.canonical_url,
                    article.url,
                    article.headline,
                    article.publisher,
                    article.source_id,
                    article.language,
                    article.published_at.isoformat() if article.published_at else None,
                    article.scraped_at.isoformat(),
                    article.summary,
                    article.content,
                    json.dumps(article.entities, ensure_ascii=False),
                    json.dumps(article.matched_topics, ensure_ascii=False),
                    json.dumps(article.matched_keywords, ensure_ascii=False),
                    article.relevance_score,
                    article.discovery_method,
                    article.content_hash,
                    article.title_fingerprint,
                ),
            )
            return cur.rowcount > 0

    def save_run_report(self, report: ScrapeRunReport) -> None:
        """Persist a scrape run summary for observability and auditing."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scrape_runs (
                    run_id, started_at, finished_at, lookback_days,
                    total_discovered, total_relevant, total_saved, total_duplicates,
                    report_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.run_id,
                    report.started_at.isoformat(),
                    report.finished_at.isoformat(),
                    report.lookback_days,
                    report.total_discovered,
                    report.total_relevant,
                    report.total_saved,
                    report.total_duplicates,
                    json.dumps(report.to_dict(), ensure_ascii=False),
                ),
            )

    def fetch_all_articles(self) -> list[dict[str, object]]:
        """Retrieve all stored articles ordered newest-first as structured dictionaries."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM articles ORDER BY COALESCE(published_at, scraped_at) DESC"
            ).fetchall()
        result: list[dict[str, object]] = []
        for r in rows:
            result.append({
                "id": r["id"],
                "headline": r["headline"],
                "url": r["url"],
                "canonical_url": r["canonical_url"],
                "publisher": r["publisher"],
                "source_id": r["source_id"],
                "language": r["language"],
                "published_at": r["published_at"],
                "scraped_at": r["scraped_at"],
                "summary": r["summary"],
                "content": r["content"],
                "entities": json.loads(r["entities_json"]),
                "matched_topics": json.loads(r["matched_topics_json"]),
                "matched_keywords": json.loads(r["matched_keywords_json"]),
                "relevance_score": r["relevance_score"],
                "discovery_method": r["discovery_method"],
            })
        return result

    def export_json(self, output_path: str | Path) -> int:
        """Export all stored articles to a UTF-8 JSON file. Returns count exported."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        articles = self.fetch_all_articles()
        with path.open("w", encoding="utf-8") as fh:
            json.dump(articles, fh, ensure_ascii=False, indent=2)
        return len(articles)

    def export_csv(self, output_path: str | Path) -> int:
        """Export all stored articles to a UTF-8 CSV file. Returns count exported."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        articles = self.fetch_all_articles()
        fieldnames = [
            "id",
            "published_at",
            "publisher",
            "language",
            "headline",
            "url",
            "entities",
            "matched_topics",
            "relevance_score",
            "summary",
            "content",
            "discovery_method",
            "scraped_at",
        ]
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for a in articles:
                writer.writerow({
                    "id": a["id"],
                    "published_at": a["published_at"] or "",
                    "publisher": a["publisher"],
                    "language": a["language"],
                    "headline": a["headline"],
                    "url": a["url"],
                    "entities": "; ".join(a["entities"]),  # type: ignore[arg-type]
                    "matched_topics": "; ".join(a["matched_topics"]),  # type: ignore[arg-type]
                    "relevance_score": a["relevance_score"],
                    "summary": a["summary"],
                    "content": a["content"],
                    "discovery_method": a["discovery_method"],
                    "scraped_at": a["scraped_at"],
                })
        return len(articles)
