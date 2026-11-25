"""Integration and unit tests for the local LLM intelligence engine, Daily Brief, and Dashboard service."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from bd_telecom_scraper.dashboard.data_service import DashboardDataService
from bd_telecom_scraper.discovery import DHAKA_TZ
from bd_telecom_scraper.llm.briefing import DailyBriefingEngine, cluster_related_articles
from bd_telecom_scraper.llm.client import OllamaUnavailableError, StructuredLLMError
from bd_telecom_scraper.llm.config import LLMConfig
from bd_telecom_scraper.llm.pipeline import ArticleIntelligencePipeline
from bd_telecom_scraper.llm.schemas import ArticleIntelligence, BriefingDevelopment, DailyBriefingOutput
from bd_telecom_scraper.models import Article
from bd_telecom_scraper.storage import ArticleStore


def _seed_articles(db_path: Path) -> None:
    store = ArticleStore(db_path)
    samples = [
        Article(
            url="https://www.tbsnews.net/tech/btrc-5g-spectrum",
            canonical_url="https://www.tbsnews.net/tech/btrc-5g-spectrum",
            headline="BTRC finalizes 5G spectrum renewal terms for mobile operators",
            publisher="The Business Standard",
            source_id="tbsnews",
            language="en",
            published_at=datetime(2026, 11, 22, 10, 0, tzinfo=DHAKA_TZ),
            scraped_at=datetime(2026, 11, 22, 11, 0, tzinfo=DHAKA_TZ),
            summary="BTRC revised spectrum renewal terms for Grameenphone, Robi, and Banglalink.",
            content="The Bangladesh Telecommunication Regulatory Commission (BTRC) has finalized revised 5G spectrum renewal terms for Grameenphone, Robi, and Banglalink covering 700 MHz and 3.5 GHz bands.",
            entities=["BTRC", "Grameenphone", "Robi", "Banglalink"],
            matched_topics=["regulation_btrc", "infrastructure_5g"],
            matched_keywords=["btrc", "5g", "spectrum"],
            relevance_score=9.0,
            discovery_method="rss",
            content_hash="hash_en_1",
            title_fingerprint="fp_1",
        ),
        Article(
            url="https://www.prothomalo.com/business/bn-btrc-spectrum",
            canonical_url="https://www.prothomalo.com/business/bn-btrc-spectrum",
            headline="বিটিআরসি ৫জি স্পেকট্রাম নবায়ন নীতিমালা চূড়ান্ত করেছে",
            publisher="Prothom Alo (Bangla)",
            source_id="prothom_alo_bn",
            language="bn",
            published_at=datetime(2026, 11, 22, 12, 0, tzinfo=DHAKA_TZ),
            scraped_at=datetime(2026, 11, 22, 13, 0, tzinfo=DHAKA_TZ),
            summary="গ্রামীণফোন, রবি ও বাংলালিংকের জন্য নতুন তরঙ্গ নবায়ন নীতিমালা।",
            content="বাংলাদেশ টেলিযোগাযোগ নিয়ন্ত্রণ কমিশন (বিটিআরসি) গ্রামীণফোন, রবি এবং বাংলালিংকের জন্য ৫জি তরঙ্গ নবায়নের সংশোধিত নীতিমালা চূড়ান্ত করেছে।",
            entities=["BTRC", "Grameenphone", "Robi", "Banglalink"],
            matched_topics=["regulation_btrc", "infrastructure_5g"],
            matched_keywords=["বিটিআরসি", "৫জি", "গ্রামীণফোন"],
            relevance_score=9.5,
            discovery_method="quintype_search",
            content_hash="hash_bn_2",
            title_fingerprint="fp_2",
        ),
        Article(
            url="https://www.dhakatribune.com/business/short-broken",
            canonical_url="https://www.dhakatribune.com/business/short-broken",
            headline="Short stub",
            publisher="Dhaka Tribune",
            source_id="dhaka_tribune",
            language="en",
            published_at=datetime(2026, 11, 14, 10, 0, tzinfo=DHAKA_TZ),
            scraped_at=datetime(2026, 11, 14, 11, 0, tzinfo=DHAKA_TZ),
            summary="Too short",
            content="Too short",
            entities=[],
            matched_topics=[],
            matched_keywords=[],
            relevance_score=2.0,
            discovery_method="rss",
            content_hash="hash_short_3",
            title_fingerprint="fp_3",
        ),
    ]
    for a in samples:
        store.save_article(a)


class _StubOllamaClient:
    def __init__(self, *, mode: str = "ok") -> None:
        self.mode = mode
        self.calls = 0

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_cls: type[Any],
        model_override: str | None = None,
    ) -> tuple[Any, int, int]:
        self.calls += 1
        if self.mode == "unavailable":
            raise OllamaUnavailableError("Ollama daemon is not running")
        if self.mode == "malformed":
            raise StructuredLLMError("Invalid JSON from model")

        if schema_cls is ArticleIntelligence:
            return (
                ArticleIntelligence(
                    english_headline="BTRC Finalizes 5G Spectrum Renewal Terms for Mobile Operators",
                    summary=(
                        "The Bangladesh Telecommunication Regulatory Commission has finalized revised 5G "
                        "spectrum renewal guidelines covering Grameenphone, Robi, and Banglalink. "
                        "The updated framework addresses both 700 MHz and 3.5 GHz spectrum bands. "
                        "Operators now have clearer regulatory terms for commercial 5G deployment."
                    ),
                    key_takeaways=[
                        "BTRC finalized revised 5G spectrum renewal terms.",
                        "Grameenphone, Robi, and Banglalink are covered by the policy.",
                        "Both 700 MHz and 3.5 GHz bands are included.",
                    ],
                    main_category="Spectrum & Licensing",
                    subcategory="5G Spectrum Renewal",
                    organizations=["BTRC", "Grameenphone", "Robi", "Banglalink"],
                    operators_mentioned=["GP", "Robi Axiata", "Banglalink"],
                    sentiment="Positive",
                    sentiment_confidence=0.84,
                    industry_impact="High",
                    telecom_relevance="High",
                    reported_facts=["700 MHz and 3.5 GHz bands included"],
                    industry_implications="Clearer renewal terms reduce regulatory uncertainty for 5G capex.",
                ),
                180,
                1,
            )

        return (
            DailyBriefingOutput(
                headline="BTRC Finalizes 5G Spectrum Renewal Rules Across Major Operators",
                intro_overview="Regulators moved forward on spectrum policy affecting Bangladesh's top three mobile operators.",
                developments=[
                    BriefingDevelopment(
                        title="BTRC Sets Revised 5G Spectrum Renewal Framework",
                        reported_facts="BTRC finalized revised renewal terms covering 700 MHz and 3.5 GHz bands for Grameenphone, Robi, and Banglalink.",
                        why_it_matters="Provides predictable licensing terms required to unlock commercial 5G network investments.",
                        key_entities=["BTRC", "Grameenphone", "Robi", "Banglalink"],
                        supporting_article_ids=[1, 2, 99999],  # 99999 must be sanitized out!
                    )
                ],
                overall_implications="Regulatory clarity is improving for 5G rollout planning in Bangladesh.",
            ),
            240,
            1,
        )


def test_llm_pipeline_incremental_failure_isolation_and_reprocessing(tmp_path: Path) -> None:
    db_path = tmp_path / "test_intel.db"
    _seed_articles(db_path)

    cfg = LLMConfig(model_name="qwen2.5:3b", batch_size=10)
    stub = _StubOllamaClient(mode="ok")
    pipeline = ArticleIntelligencePipeline(db_path=db_path, config=cfg, client=stub)

    # First run: 2 valid articles complete, 1 short stub fails cleanly without stopping batch
    res = pipeline.process_batch(limit=10)
    assert res.attempted == 3
    assert res.completed == 2
    assert res.failed == 1
    assert stub.calls == 2

    # Second run: unchanged completed articles MUST NOT be reprocessed
    res2 = pipeline.process_batch(limit=10)
    assert res2.completed == 0
    assert stub.calls == 2

    # Explicit reprocessing of article #1 works
    res3 = pipeline.process_batch(article_ids=[1], force_reprocess=True)
    assert res3.completed == 1
    assert stub.calls == 3

    # Verify canonical operator normalization ('GP' -> 'Grameenphone', 'Robi Axiata' -> 'Robi')
    service = DashboardDataService(db_path)
    lead = service.get_lead_story()
    assert lead is not None
    assert "Grameenphone" in lead["operators"]
    assert "Robi" in lead["operators"]


def test_ollama_unavailable_and_malformed_output_handling(tmp_path: Path) -> None:
    db_path = tmp_path / "err_intel.db"
    _seed_articles(db_path)
    cfg = LLMConfig(model_name="qwen2.5:3b")

    # Ollama down stops batch cleanly and records error
    p_down = ArticleIntelligencePipeline(db_path=db_path, config=cfg, client=_StubOllamaClient(mode="unavailable"))
    res = p_down.process_batch(limit=5)
    assert res.ollama_unavailable is True
    assert res.failed == 1

    # Malformed LLM output isolates failure and continues
    p_bad = ArticleIntelligencePipeline(db_path=db_path, config=cfg, client=_StubOllamaClient(mode="malformed"))
    res_bad = p_bad.process_batch(article_ids=[1, 2], force_reprocess=True)
    assert res_bad.failed == 2
    assert len(p_bad.repo.get_recent_errors()) >= 2


def test_daily_briefing_clustering_id_sanitization_and_dashboard_queries(tmp_path: Path) -> None:
    db_path = tmp_path / "brief_intel.db"
    _seed_articles(db_path)
    cfg = LLMConfig(model_name="qwen2.5:3b")
    stub = _StubOllamaClient(mode="ok")

    ArticleIntelligencePipeline(db_path=db_path, config=cfg, client=stub).process_batch(limit=10)

    # Confirm duplicate English + Bangla coverage clusters into ONE development
    engine = DailyBriefingEngine(db_path=db_path, config=cfg, client=stub)
    brief = engine.generate_briefing(target_date="2026-11-22")
    assert brief["status"] == "COMPLETED"
    assert len(brief["developments"]) == 1
    # Hallucinated article ID 99999 must be stripped
    assert 99999 not in brief["developments"][0]["supporting_article_ids"]
    assert set(brief["developments"][0]["supporting_article_ids"]) == {1, 2}

    # Cached reuse on second call (no extra LLM call)
    calls_before = stub.calls
    brief2 = engine.generate_briefing(target_date="2026-11-22")
    assert brief2["headline"] == brief["headline"]
    assert stub.calls == calls_before

    # Dashboard filtering, search, and industry signals
    service = DashboardDataService(db_path)
    feed = service.get_intelligence_feed(publisher="Prothom Alo (Bangla)", search_query="spectrum")
    assert len(feed) == 1
    signals = service.get_industry_signals(window_days=7)
    assert len(signals["topics"]) >= 1
    assert any(op["operator"] == "Grameenphone" for op in signals["operators"])
