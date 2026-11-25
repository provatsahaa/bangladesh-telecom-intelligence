"""Unit tests for the standalone bd_telecom_scraper module."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

from bd_telecom_scraper.deduplicator import (
    Deduplicator,
    canonicalize_url,
    content_hash,
    title_fingerprint,
)
from bd_telecom_scraper.discovery import (
    DHAKA_TZ,
    _parse_sitemap_urlset,
    _select_relevant_child_sitemaps,
    parse_datetime_flexible,
)
from bd_telecom_scraper.extractor import extract_article_from_html
from bd_telecom_scraper.filtering import (
    candidate_looks_promising,
    evaluate_relevance,
    extract_entities,
)
from bd_telecom_scraper.models import Article, SourceConfig
from bd_telecom_scraper.storage import ArticleStore


def test_bilingual_relevance_and_entity_extraction() -> None:
    en_res = evaluate_relevance(
        headline="BTRC approves 5G spectrum allocation for Grameenphone and Robi",
        summary="Banglalink and Teletalk also submitted proposals to the telecom regulator.",
    )
    assert en_res.is_relevant is True
    assert en_res.score >= 6.0
    assert {"BTRC", "Grameenphone", "Robi", "Banglalink", "Teletalk"}.issubset(set(en_res.entities))
    assert "operators" in en_res.matched_topics
    assert "regulation_btrc" in en_res.matched_topics
    assert "infrastructure_5g" in en_res.matched_topics

    bn_res = evaluate_relevance(
        headline="বিটিআরসির নতুন তরঙ্গ নিলামে অংশ নিচ্ছে গ্রামীণফোন ও বাংলালিংক",
        summary="৫জি সেবা চালুর লক্ষ্যে মোবাইল অপারেটরদের সাথে বৈঠক।",
    )
    assert bn_res.is_relevant is True
    assert {"BTRC", "Grameenphone", "Banglalink"}.issubset(set(bn_res.entities))

    # Non-telecom article should be rejected (even if it contains 'AI' or 'daily')
    noise = evaluate_relevance(
        headline="Bangladesh cricket team prepares for upcoming test series in Dhaka",
        summary="The captain praised the daily training regime and new batting coach.",
    )
    assert noise.is_relevant is False
    assert extract_entities(noise.matched_keywords[0] if noise.matched_keywords else "") == []


def test_url_canonicalization_and_syndicated_deduplication() -> None:
    u1 = canonicalize_url("https://www.tbsnews.net/tech/btrc-5g-rollout/?utm_source=fb&fbclid=123#top")
    u2 = canonicalize_url("https://m.tbsnews.net/tech/btrc-5g-rollout")
    assert u1 == "https://www.tbsnews.net/tech/btrc-5g-rollout"
    assert u2 == "https://www.tbsnews.net/tech/btrc-5g-rollout"

    dedup = Deduplicator()
    h1 = content_hash("BTRC launches new 5G spectrum auction for mobile operators", "Body text A")
    dedup.register(u1, "BTRC launches new 5G spectrum auction for mobile operators", h1)

    # Same URL
    dup, reason = dedup.is_duplicate(u1, "Different title", "otherhash")
    assert dup and reason == "duplicate_url"

    # Syndicated wire story on another portal with near-identical headline
    dup2, reason2 = dedup.is_duplicate(
        "https://www.thedailystar.net/tech-startup/btrc-5g-auction",
        "BTRC launches new 5G spectrum auction for mobile operators in Bangladesh",
        "differenthash",
    )
    assert dup2 and reason2 is not None and "duplicate_syndicated_title" in reason2


def test_sitemap_selection_and_14_day_window() -> None:
    now = datetime(2026, 11, 20, 12, 0, tzinfo=DHAKA_TZ)
    cutoff = now - timedelta(days=14)

    child_locs = [
        "https://en.prothomalo.com/sitemap/sitemap-static-pages.xml",
        "https://en.prothomalo.com/sitemap/sitemap-daily-2026-11-20.xml",
        "https://en.prothomalo.com/sitemap/sitemap-daily-2026-11-10.xml",
        "https://en.prothomalo.com/sitemap/sitemap-daily-2026-10-01.xml",
        "https://en.prothomalo.com/news_sitemap.xml",
    ]
    selected = _select_relevant_child_sitemaps(child_locs, cutoff=cutoff, now=now)
    assert "https://en.prothomalo.com/news_sitemap.xml" in selected
    assert "https://en.prothomalo.com/sitemap/sitemap-daily-2026-11-20.xml" in selected
    assert "https://en.prothomalo.com/sitemap/sitemap-daily-2026-11-10.xml" in selected
    assert "https://en.prothomalo.com/sitemap/sitemap-daily-2026-10-01.xml" not in selected

    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
            xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
      <url>
        <loc>https://en.prothomalo.com/business/grameenphone-5g-launch</loc>
        <lastmod>2026-11-18T10:00:00+06:00</lastmod>
        <news:news>
          <news:publication_date>2026-11-18T10:00:00+06:00</news:publication_date>
          <news:title>Grameenphone accelerates 5G network rollout</news:title>
        </news:news>
      </url>
      <url>
        <loc>https://en.prothomalo.com/business/old-telecom-news</loc>
        <lastmod>2026-10-01T10:00:00+06:00</lastmod>
      </url>
    </urlset>"""
    src = SourceConfig(
        source_id="prothom_alo_en",
        publisher="Prothom Alo (English)",
        language="en",
        base_url="https://en.prothomalo.com",
    )
    items = _parse_sitemap_urlset(xml, src, cutoff=cutoff)
    assert len(items) == 1
    assert items[0].headline == "Grameenphone accelerates 5G network rollout"
    assert candidate_looks_promising(items[0].url, items[0].headline) is True
    # Random alphanumeric Quintype story IDs containing '4g' or '5g' must NOT match
    assert candidate_looks_promising("https://en.prothomalo.com/sports/cricket/ganp5g0tpe") is False
    assert candidate_looks_promising("https://en.prothomalo.com/bangladesh/4g642f69uc") is False
    assert candidate_looks_promising("https://www.tbsnews.net/tech/btrc-launches-5g-trial-12345") is True


def test_html_json_ld_extraction_and_storage_exports(tmp_path: Path) -> None:
    html = """
    <html>
      <head>
        <title>Robi expands 4G network</title>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "NewsArticle",
          "headline": "Robi Axiata expands 4G coverage across rural Bangladesh",
          "datePublished": "2026-11-19T14:30:00+06:00",
          "description": "Robi deployed 500 new 4G towers in partnership with Edotco.",
          "articleBody": "Robi Axiata announced today that it has expanded its 4G LTE network across 20 rural districts in Bangladesh with BTRC approval and Edotco tower sharing."
        }
        </script>
      </head>
      <body><article><p>Robi Axiata announced today that it has expanded its 4G LTE network across 20 rural districts.</p></article></body>
    </html>
    """
    extracted = extract_article_from_html(html, "https://example.com/robi-4g")
    assert "Robi Axiata" in extracted.headline
    assert extracted.published_at == parse_datetime_flexible("2026-11-19T14:30:00+06:00")
    assert "Edotco" in extracted.summary

    db_path = tmp_path / "test_telecom.db"
    store = ArticleStore(db_path)
    article = Article(
        url="https://example.com/robi-4g",
        canonical_url="https://example.com/robi-4g",
        headline=extracted.headline,
        publisher="Test Portal",
        source_id="test_portal",
        language="en",
        published_at=extracted.published_at,
        scraped_at=datetime.now(DHAKA_TZ),
        summary=extracted.summary,
        content=extracted.content,
        entities=["Robi", "BTRC", "Edotco Bangladesh"],
        matched_topics=["operators", "infrastructure_5g"],
        matched_keywords=["robi axiata", "4g", "btrc"],
        relevance_score=9.5,
        discovery_method="rss",
        content_hash=content_hash(extracted.headline, extracted.content),
        title_fingerprint=title_fingerprint(extracted.headline),
    )
    assert store.save_article(article) is True
    # Duplicate insert must be ignored cleanly
    assert store.save_article(article) is False

    json_out = tmp_path / "articles.json"
    csv_out = tmp_path / "articles.csv"
    assert store.export_json(json_out) == 1
    assert store.export_csv(csv_out) == 1

    loaded_json = json.loads(json_out.read_text(encoding="utf-8"))
    assert loaded_json[0]["headline"] == extracted.headline
    assert "Robi" in loaded_json[0]["entities"]

    with csv_out.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert "Edotco Bangladesh" in rows[0]["entities"]
