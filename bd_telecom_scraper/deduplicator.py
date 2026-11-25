"""Deduplication utilities for URLs, exact content, and syndicated/republished stories."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source", "amp",
})

_NON_WORD_RE = re.compile(r"[^\w\s\u0980-\u09FF]+", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def canonicalize_url(url: str) -> str:
    """Normalize a URL by stripping tracking query params, fragments, and trailing slashes."""
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("m."):
        netloc = "www." + netloc[2:]
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/") or "/"
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs))
    return urlunparse((scheme, netloc, path, "", query, ""))


def normalize_title(headline: str) -> str:
    """Produce a normalized title string for fuzzy/syndicated duplicate detection."""
    text = unicodedata.normalize("NFKC", headline or "").lower().strip()
    text = _NON_WORD_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def title_fingerprint(headline: str) -> str:
    """Short SHA-256 fingerprint of a normalized headline."""
    norm = normalize_title(headline)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def content_hash(headline: str, content: str) -> str:
    """SHA-256 hash of normalized headline + first 1,500 chars of body text."""
    body_snippet = _WHITESPACE_RE.sub(" ", (content or "").strip())[:1500].lower()
    payload = f"{normalize_title(headline)}||{body_snippet}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Deduplicator:
    """Tracks seen articles across URL, content hash, and near-duplicate/syndicated titles."""

    def __init__(self, title_similarity_threshold: float = 0.86) -> None:
        self.title_similarity_threshold = title_similarity_threshold
        self._seen_urls: set[str] = set()
        self._seen_hashes: set[str] = set()
        self._seen_titles: list[str] = []

    def seed(self, canonical_urls: set[str], content_hashes: set[str], titles: list[str]) -> None:
        """Pre-populate deduplicator with existing articles from storage."""
        self._seen_urls.update(canonical_urls)
        self._seen_hashes.update(content_hashes)
        self._seen_titles.extend(normalize_title(t) for t in titles if t)

    def is_duplicate(self, canonical_url: str, headline: str, c_hash: str) -> tuple[bool, str | None]:
        """Check whether an article is a duplicate (exact URL, content hash, or syndicated title)."""
        if canonical_url in self._seen_urls:
            return True, "duplicate_url"
        if c_hash and c_hash in self._seen_hashes:
            return True, "duplicate_content_hash"

        norm_title = normalize_title(headline)
        if len(norm_title) >= 20:
            for existing in self._seen_titles:
                if existing == norm_title:
                    return True, "duplicate_exact_title"
                # Quick length ratio filter before SequenceMatcher
                len_ratio = min(len(norm_title), len(existing)) / max(len(norm_title), len(existing))
                if len_ratio >= 0.75:
                    sim = SequenceMatcher(None, norm_title, existing).ratio()
                    if sim >= self.title_similarity_threshold:
                        return True, f"duplicate_syndicated_title({sim:.2f})"
        return False, None

    def register(self, canonical_url: str, headline: str, c_hash: str) -> None:
        """Record an accepted article so subsequent duplicates are caught."""
        self._seen_urls.add(canonical_url)
        if c_hash:
            self._seen_hashes.add(c_hash)
        norm_title = normalize_title(headline)
        if norm_title:
            self._seen_titles.append(norm_title)
