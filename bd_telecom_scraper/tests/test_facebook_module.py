"""Unit tests for the compliant Facebook Page telecom intelligence module."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx

from bd_telecom_scraper.discovery import DHAKA_TZ
from bd_telecom_scraper.facebook.graph_client import MetaGraphClient
from bd_telecom_scraper.facebook.models import FacebookSource
from bd_telecom_scraper.facebook.pipeline import FacebookIntelPipeline
from bd_telecom_scraper.facebook.source_registry import DEFAULT_FACEBOOK_SOURCES
from bd_telecom_scraper.models import Article
from bd_telecom_scraper.storage import ArticleStore


def test_source_registry_matches_approved_24_pages() -> None:
    assert len(DEFAULT_FACEBOOK_SOURCES) == 24
    names = {s.page_name for s in DEFAULT_FACEBOOK_SOURCES}
    assert "Cablegram" in names
    for removed in ("Telecom Review", "TelecomTV", "Developing Telecoms", "Capacity Media"):
        assert removed not in names

    by_id = {s.source_id: s for s in DEFAULT_FACEBOOK_SOURCES}
    assert by_id["fb_grameenphone"].verification_status == "VERIFIED_OFFICIAL_SITE"
    assert by_id["fb_grameenphone"].facebook_page_id == "135237519825044"
    assert by_id["fb_cablegram"].verification_status == "UNVERIFIED_NEEDS_CONFIRMATION"
    assert by_id["fb_cablegram"].facebook_url is None
    assert all(s.access_mode == "manual_only" for s in DEFAULT_FACEBOOK_SOURCES)


def test_graph_api_v27_pagination_and_ppca_permission_handling(tmp_path: Path) -> None:
    db_path = tmp_path / "intel.db"
    pipeline = FacebookIntelPipeline(db_path=db_path)

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/135237519825044/feed" in url_str and "cursor2" not in url_str:
            return httpx.Response(
                200,
                headers={"x-app-usage": '{"call_count": 10, "total_cputime": 5, "total_time": 5}'},
                json={
                    "data": [
                        {
                            "id": "135237519825044_1001",
                            "message": "Grameenphone partners with Ericsson to expand 5G RAN and core network in Dhaka.",
                            "created_time": "2026-11-20T10:00:00+0000",
                            "permalink_url": "https://www.facebook.com/grameenphone/posts/1001",
                            "shares": {"count": 42},
                            "reactions": {"summary": {"total_count": 320}},
                            "comments": {"summary": {"total_count": 18}},
                            "attachments": {
                                "data": [{"type": "photo", "media_type": "photo", "url": "https://fb.com/photo1"}]
                            },
                        },
                        {
                            "id": "135237519825044_1002",
                            "message": "Happy Friday everyone! Enjoy the weekend weather.",
                            "created_time": "2026-11-19T10:00:00+0000",
                            "permalink_url": "https://www.facebook.com/grameenphone/posts/1002",
                        },
                    ],
                    "paging": {
                        "next": "https://graph.facebook.com/v27.0/135237519825044/feed?after=cursor2"
                    },
                },
            )
        if "cursor2" in url_str:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "135237519825044_1003",
                            "message": "বিটিআরসি অনুমোদিত নতুন ৪জি ও ৫জি স্পেকট্রাম বরাদ্দ নিয়ে গ্রামীণফোনের ঘোষণা।",
                            "created_time": "2026-11-18T08:30:00+0000",
                            "permalink_url": "https://www.facebook.com/grameenphone/posts/1003",
                        }
                    ]
                },
            )
        # Simulate PPCA access denial on third-party Page
        return httpx.Response(
            403,
            json={
                "error": {
                    "message": "This endpoint requires the 'Page Public Content Access' feature.",
                    "type": "OAuthException",
                    "code": 10,
                }
            },
        )

    mock_http = httpx.Client(transport=httpx.MockTransport(handler))
    graph = MetaGraphClient(access_token="test-token", http_client=mock_http)

    managed_src = FacebookSource(
        source_id="fb_grameenphone",
        page_name="Grameenphone",
        category="mno",
        official_website="https://www.grameenphone.com",
        facebook_url="https://www.facebook.com/grameenphone",
        facebook_page_id="135237519825044",
        verification_status="VERIFIED_OFFICIAL_SITE",
        access_mode="managed_api",
    )
    ppca_src = FacebookSource(
        source_id="fb_btrc",
        page_name="BTRC",
        category="regulator_org",
        official_website="https://btrc.gov.bd",
        facebook_url="https://www.facebook.com/btrc.gov",
        facebook_page_id="999999",
        verification_status="VERIFIED_OFFICIAL_SITE",
        access_mode="ppca_api",
    )

    report = pipeline.sync_via_graph_api(
        sources_override=[managed_src, ppca_src],
        graph_client=graph,
    )
    assert report.source_statuses["fb_grameenphone"] == "OK"
    assert report.source_statuses["fb_btrc"] == "PERMISSION_REQUIRED"
    assert report.posts_fetched == 3
    assert report.posts_relevant == 2
    assert report.posts_saved == 2

    posts = pipeline.store.fetch_all_posts()
    assert len(posts) == 2
    assert posts[0]["collection_method"] == "graph_api_managed"
    assert posts[0]["engagement"] == {"reactions": 320, "comments": 18, "shares": 42}
    assert "Ericsson" in posts[0]["entities"]
    assert "Grameenphone" in posts[0]["entities"]
    assert posts[1]["language"] == "bn"
    assert "BTRC" in posts[1]["entities"]


def test_manual_ingestion_batch_unified_view_and_purge(tmp_path: Path) -> None:
    db_path = tmp_path / "shared_intel.db"

    # 1. Seed one existing news portal article to verify unified_telecom_intel view
    news_store = ArticleStore(db_path)
    news_store.save_article(
        Article(
            url="https://www.tbsnews.net/tech/btrc-spectrum-update",
            canonical_url="https://www.tbsnews.net/tech/btrc-spectrum-update",
            headline="BTRC finalizes 5G spectrum roadmap",
            publisher="The Business Standard",
            source_id="tbsnews",
            language="en",
            published_at=datetime(2026, 11, 20, 12, 0, tzinfo=DHAKA_TZ),
            scraped_at=datetime(2026, 11, 20, 13, 0, tzinfo=DHAKA_TZ),
            summary="BTRC published the updated 5G spectrum guidelines.",
            content="BTRC published the updated 5G spectrum guidelines for mobile operators.",
            entities=["BTRC"],
            matched_topics=["regulation_btrc", "infrastructure_5g"],
            matched_keywords=["btrc", "5g"],
            relevance_score=7.5,
            discovery_method="rss",
            content_hash="abc123",
            title_fingerprint="fp123",
        )
    )

    # 2. Manually ingest a public Facebook post (no fabricated page_id/engagement)
    pipeline = FacebookIntelPipeline(db_path=db_path)
    saved, post, reason = pipeline.ingest_manual_post(
        post_url="https://www.facebook.com/banglalinkdigital/posts/pfbid012345?utm_source=share",
        post_text="Banglalink expands 4G & Huawei fiber network across Sylhet with BTRC approval.",
        published_at="2026-11-21T15:00:00+06:00",
    )
    assert saved is True
    assert reason == "saved"
    assert post is not None
    assert post.source_id == "fb_banglalink"
    assert post.page_name == "Banglalink"
    assert post.collection_method == "manual_submission"
    assert post.engagement is None
    assert post.post_id is None
    assert {"Banglalink", "Huawei", "BTRC"}.issubset(set(post.entities))

    # 3. Batch JSON ingestion with a duplicate + new Bangla post
    batch_file = tmp_path / "manual_batch.json"
    batch_file.write_text(
        json.dumps([
            {
                "post_url": "https://www.facebook.com/banglalinkdigital/posts/pfbid012345",
                "post_text": "Banglalink expands 4G & Huawei fiber network across Sylhet with BTRC approval.",
            },
            {
                "post_url": "https://www.facebook.com/DailyProthomAlo/posts/pfbid999",
                "post_text": "রবি আজিয়াটা ও নকিয়া বাংলাদেশে নতুন ৫জি কোর নেটওয়ার্ক পরীক্ষামূলকভাবে চালু করেছে।",
            },
        ]),
        encoding="utf-8",
    )
    batch_report = pipeline.ingest_manual_batch(batch_file)
    assert batch_report.posts_saved == 1
    assert batch_report.posts_duplicate == 1

    # 4. Verify unified SQL view combines news_portal + facebook_page rows
    unified = pipeline.store.fetch_unified_intel()
    channels = {row["channel"] for row in unified}
    assert channels == {"news_portal", "facebook_page"}
    assert len(unified) == 3

    # 5. Verify exports and data-deletion compliance purge
    assert pipeline.store.export_json(tmp_path / "fb.json") == 2
    assert pipeline.store.export_csv(tmp_path / "fb.csv") == 2
    purged = pipeline.store.purge_source("fb_banglalink")
    assert purged == 1
    assert len(pipeline.store.fetch_all_posts()) == 1
