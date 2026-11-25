"""Method 2: Compliant Manual Public Content Ingestion for Facebook Page posts.

Allows users to submit publicly accessible Facebook post URLs and pasted post text
(single CLI submission or batch JSON/CSV file) when Graph API PPCA access is unavailable.
Never fabricates missing metadata (e.g., page_id, post_id, engagement, media_refs).
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from ..discovery import parse_datetime_flexible
from .source_registry import match_source_by_url

_VALID_FB_HOSTS = frozenset({
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "web.facebook.com",
    "fb.watch",
    "www.fb.watch",
})

_BANGLA_CHAR_RE = re.compile(r"[\u0980-\u09FF]")
_LATIN_CHAR_RE = re.compile(r"[A-Za-z]")


@dataclass
class ManualPostSubmission:
    """Validated manual submission payload for a public Facebook post."""

    post_url: str
    post_text: str
    page_name: str
    source_id: str | None = None
    page_id: str | None = None
    post_id: str | None = None
    published_at: datetime | None = None
    media_refs: list[dict[str, str]] | None = None


def validate_facebook_post_url(url: str) -> str:
    """Validate that a submitted URL is a well-formed public Facebook URL."""
    cleaned = (url or "").strip()
    if not cleaned:
        raise ValueError("Facebook post URL is required.")
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme for Facebook post: {cleaned}")
    host = parsed.netloc.lower()
    if host not in _VALID_FB_HOSTS:
        raise ValueError(f"URL must point to facebook.com or fb.watch, got: {host}")
    if not parsed.path or parsed.path == "/":
        raise ValueError(f"Facebook URL must reference a specific post or Page path: {cleaned}")
    return cleaned


def detect_language(text: str) -> str:
    """Classify text language as 'bn', 'en', or 'mixed' based on script distribution."""
    bn_count = len(_BANGLA_CHAR_RE.findall(text or ""))
    en_count = len(_LATIN_CHAR_RE.findall(text or ""))
    if bn_count > 0 and en_count > 0:
        ratio = min(bn_count, en_count) / max(bn_count, en_count)
        if ratio >= 0.2:
            return "mixed"
    return "bn" if bn_count > en_count else "en"


def build_manual_submission(
    *,
    post_url: str,
    post_text: str,
    page_name: str | None = None,
    published_at: str | datetime | None = None,
    source_id: str | None = None,
    post_id: str | None = None,
) -> ManualPostSubmission:
    """Construct and validate a ManualPostSubmission, auto-matching verified registry Pages."""
    valid_url = validate_facebook_post_url(post_url)
    text = (post_text or "").strip()
    if not text:
        raise ValueError("Post text cannot be empty.")

    matched = match_source_by_url(valid_url)
    resolved_source_id = source_id or (matched.source_id if matched else None)
    resolved_page_name = (page_name or "").strip() or (matched.page_name if matched else "Unknown Facebook Page")
    resolved_page_id = matched.facebook_page_id if matched else None

    if isinstance(published_at, datetime):
        parsed_dt = published_at
    else:
        parsed_dt = parse_datetime_flexible(published_at)

    return ManualPostSubmission(
        post_url=valid_url,
        post_text=text,
        page_name=resolved_page_name,
        source_id=resolved_source_id,
        page_id=resolved_page_id,
        post_id=(post_id or "").strip() or None,
        published_at=parsed_dt,
        media_refs=None,
    )


def load_manual_batch_file(path: str | Path) -> list[ManualPostSubmission]:
    """Load manual Facebook post submissions from a JSON or CSV file.

    Required fields per record: `post_url`, `post_text`.
    Optional fields: `page_name`, `published_at`, `source_id`, `post_id`.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Manual batch file not found: {file_path}")

    suffix = file_path.suffix.lower()
    raw_records: list[dict[str, object]] = []
    if suffix == ".json":
        loaded = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            raise ValueError("JSON batch file must contain a top-level list of post objects.")
        raw_records = [r for r in loaded if isinstance(r, dict)]
    elif suffix == ".csv":
        with file_path.open(encoding="utf-8", newline="") as fh:
            raw_records = list(csv.DictReader(fh))
    else:
        raise ValueError(f"Unsupported batch file format '{suffix}'. Use .json or .csv.")

    submissions: list[ManualPostSubmission] = []
    for rec in raw_records:
        submissions.append(
            build_manual_submission(
                post_url=str(rec.get("post_url") or ""),
                post_text=str(rec.get("post_text") or ""),
                page_name=str(rec.get("page_name") or "") or None,
                published_at=str(rec.get("published_at") or "") or None,
                source_id=str(rec.get("source_id") or "") or None,
                post_id=str(rec.get("post_id") or "") or None,
            )
        )
    return submissions
