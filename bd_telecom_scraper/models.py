"""Data models for the Bangladesh Telecom News Aggregator scraping module.

Designed as clean, serializable contracts so downstream intelligence modules
(categorization, sentiment analysis, NER, trend detection, operator analytics)
can consume articles directly from Python objects, SQLite, JSON, or CSV.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SourceConfig:
    """Configuration for a single Bangladeshi news portal / edition."""

    source_id: str
    publisher: str
    language: str  # "en" or "bn"
    base_url: str
    rss_urls: tuple[str, ...] = ()
    sitemap_urls: tuple[str, ...] = ()
    section_urls: tuple[str, ...] = ()
    quintype_collections: tuple[str, ...] = ()
    quintype_search_queries: tuple[str, ...] = ()
    allowed_path_hints: tuple[str, ...] = ()
    rate_limit_seconds: float = 1.5
    respect_robots_txt: bool = True
    enabled: bool = True
    notes: str = ""


@dataclass
class DiscoveredItem:
    """Intermediate candidate discovered from RSS, sitemap, API, or section HTML."""

    url: str
    source_id: str
    publisher: str
    language: str
    discovery_method: str  # "rss", "sitemap", "quintype_api", "section_html"
    headline: str = ""
    summary: str = ""
    published_at: datetime | None = None


@dataclass
class Article:
    """Normalized, telecom-filtered article ready for storage and downstream NLP."""

    url: str
    canonical_url: str
    headline: str
    publisher: str
    source_id: str
    language: str
    published_at: datetime | None
    scraped_at: datetime
    summary: str
    content: str
    entities: list[str] = field(default_factory=list)
    matched_topics: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    relevance_score: float = 0.0
    discovery_method: str = ""
    content_hash: str = ""
    title_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize article to a JSON-friendly dictionary."""
        data = asdict(self)
        data["published_at"] = self.published_at.isoformat() if self.published_at else None
        data["scraped_at"] = self.scraped_at.isoformat()
        return data


@dataclass
class SourceHealth:
    """Per-source execution status and diagnostics for a scrape run."""

    source_id: str
    publisher: str
    status: str  # "OK", "PARTIAL", "RESTRICTED", "ERROR", "SKIPPED"
    discovered_count: int = 0
    in_window_count: int = 0
    relevant_count: int = 0
    saved_count: int = 0
    duplicate_count: int = 0
    methods_used: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScrapeRunReport:
    """Summary of a full scraping run across all configured portals."""

    run_id: str
    started_at: datetime
    finished_at: datetime
    lookback_days: int
    total_discovered: int
    total_relevant: int
    total_saved: int
    total_duplicates: int
    source_health: list[SourceHealth] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "lookback_days": self.lookback_days,
            "total_discovered": self.total_discovered,
            "total_relevant": self.total_relevant,
            "total_saved": self.total_saved,
            "total_duplicates": self.total_duplicates,
            "source_health": [s.to_dict() for s in self.source_health],
        }
