"""Data models for the compliant Facebook telecom intelligence module."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class FacebookSource:
    """Configuration and identity verification record for a target Facebook Page."""

    source_id: str
    page_name: str
    category: str  # "mno" | "regulator_org" | "telecom_media" | "bd_news"
    official_website: str
    facebook_url: str | None = None
    facebook_page_id: str | None = None
    verification_status: str = "UNVERIFIED_NEEDS_CONFIRMATION"
    verification_evidence: str = ""
    access_mode: str = "manual_only"  # "managed_api" | "ppca_api" | "manual_only"
    enabled: bool = True


@dataclass
class FacebookPost:
    """Normalized Facebook Page post (API-collected or manually ingested).

    Unavailable fields remain None rather than being fabricated.
    """

    source_id: str | None
    page_name: str
    page_id: str | None
    post_id: str | None
    post_url: str
    post_text: str
    published_at: datetime | None
    collected_at: datetime
    collection_method: str  # "graph_api_managed" | "graph_api_ppca" | "manual_submission"
    language: str  # "en" | "bn" | "mixed"
    media_refs: list[dict[str, Any]] | None = None
    engagement: dict[str, int] | None = None
    entities: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    relevance_score: float = 0.0
    relevance_classification: str = "low"  # "high" | "medium" | "low"
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["published_at"] = self.published_at.isoformat() if self.published_at else None
        data["collected_at"] = self.collected_at.isoformat()
        return data


@dataclass
class FacebookSyncState:
    """Incremental synchronization watermark for a Facebook Page source."""

    source_id: str
    last_synced_at: datetime | None = None
    last_post_published_at: datetime | None = None
    last_status: str = "MANUAL_ONLY"
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
            "last_post_published_at": (
                self.last_post_published_at.isoformat() if self.last_post_published_at else None
            ),
            "last_status": self.last_status,
            "last_error": self.last_error,
        }


@dataclass
class FacebookRunReport:
    """Summary of a Facebook Graph API sync or manual batch ingestion run."""

    run_id: str
    mode: str  # "graph_api_sync" | "manual_ingest"
    started_at: datetime
    finished_at: datetime
    sources_checked: int = 0
    posts_fetched: int = 0
    posts_relevant: int = 0
    posts_saved: int = 0
    posts_duplicate: int = 0
    source_statuses: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "sources_checked": self.sources_checked,
            "posts_fetched": self.posts_fetched,
            "posts_relevant": self.posts_relevant,
            "posts_saved": self.posts_saved,
            "posts_duplicate": self.posts_duplicate,
            "source_statuses": self.source_statuses,
            "errors": self.errors,
        }
