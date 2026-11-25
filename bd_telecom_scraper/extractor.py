"""Article content & metadata extraction: JSON-LD -> OpenGraph/Meta -> Trafilatura -> BS4."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime

import trafilatura
from bs4 import BeautifulSoup

from .discovery import parse_datetime_flexible

logger = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class ExtractedArticleData:
    """Extracted metadata and body text from an article HTML page."""

    headline: str = ""
    published_at: datetime | None = None
    summary: str = ""
    content: str = ""


def _clean_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "")).strip()


def _extract_json_ld(soup: BeautifulSoup) -> dict[str, object]:
    """Extract NewsArticle / Article fields from embedded JSON-LD scripts."""
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue

        candidates = payload if isinstance(payload, list) else [payload]
        for obj in list(candidates):
            if isinstance(obj, dict) and "@graph" in obj and isinstance(obj["@graph"], list):
                candidates.extend(obj["@graph"])

        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            t = obj.get("@type", "")
            types = t if isinstance(t, list) else [t]
            if any(str(x).lower() in ("newsarticle", "article", "reportagenewsarticle") for x in types):
                return obj
    return {}


def _meta_content(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    return ""


def extract_article_from_html(
    html: str,
    url: str,
    *,
    fallback_headline: str = "",
    fallback_summary: str = "",
    fallback_published_at: datetime | None = None,
) -> ExtractedArticleData:
    """Extract headline, publication datetime, summary, and full text from article HTML."""
    soup = BeautifulSoup(html, "html.parser")
    ld = _extract_json_ld(soup)

    # 1. Headline
    headline = _clean_text(
        str(ld.get("headline") or "")
        or _meta_content(soup, "og:title", "twitter:title")
        or (soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else "")
        or fallback_headline
    )

    # 2. Publication date/time
    pub_raw = (
        ld.get("datePublished")
        or _meta_content(
            soup,
            "article:published_time",
            "og:article:published_time",
            "publish-date",
            "pubdate",
            "datePublished",
        )
        or (soup.find("time").get("datetime", "") if soup.find("time") else "")
    )
    published_at = parse_datetime_flexible(str(pub_raw) if pub_raw else None) or fallback_published_at

    # 3. Full article body (Trafilatura -> JSON-LD articleBody -> BS4 <article> paragraphs)
    content = ""
    try:
        extracted = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        if extracted:
            content = extracted.strip()
    except Exception as exc:
        logger.debug("Trafilatura extraction error for %s: %s", url, exc)

    if len(content) < 120 and ld.get("articleBody"):
        ld_body = _clean_text(str(ld["articleBody"]))
        if len(ld_body) > len(content):
            content = ld_body

    if len(content) < 120:
        article_tag = soup.find("article") or soup
        paragraphs = [
            _clean_text(p.get_text(" ", strip=True))
            for p in article_tag.find_all("p")
            if len(p.get_text(strip=True)) >= 40
        ]
        bs_body = "\n\n".join(paragraphs)
        if len(bs_body) > len(content):
            content = bs_body

    # 4. Summary (ignore generic site-wide boilerplate meta descriptions)
    summary = _clean_text(
        str(ld.get("description") or "")
        or _meta_content(soup, "og:description", "description")
        or fallback_summary
    )
    boilerplate_markers = ("is a major source of news", "leading english language daily newspaper")
    if (not summary or any(m in summary.lower() for m in boilerplate_markers)) and content:
        summary = _clean_text(content)[:320]

    return ExtractedArticleData(
        headline=headline,
        published_at=published_at,
        summary=summary,
        content=content,
    )
