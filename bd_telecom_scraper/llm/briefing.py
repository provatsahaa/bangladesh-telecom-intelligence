"""Phase 4: Daily Intelligence Briefing Engine.

Retrieves AI-processed articles, clusters duplicate/multi-outlet coverage of the
same event, prioritizes by Bangladesh telecom impact & relevance, and generates
a strictly grounded executive Daily Brief stored in SQLite.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..discovery import DHAKA_TZ
from ..storage import open_sqlite_connection
from .client import LocalOllamaClient
from .config import LLMConfig, load_llm_config
from .prompts import BRIEFING_SYSTEM_PROMPT, build_briefing_user_prompt
from .repository import IntelligenceRepository
from .schemas import DailyBriefingOutput

logger = logging.getLogger(__name__)

_IMPACT_RANK = {"High": 3, "Medium": 2, "Low": 1}
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "into", "over", "after",
    "bangladesh", "telecom", "new", "says", "said", "will", "has", "have", "are",
})


def _headline_tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if len(w) >= 3 and w not in _STOPWORDS}


def cluster_related_articles(
    articles: list[dict[str, Any]],
    *,
    max_clusters: int = 5,
) -> list[list[dict[str, Any]]]:
    """Conservatively group articles covering the same underlying event."""
    # Sort by impact -> relevance -> recency first so the strongest article leads each cluster
    ranked = sorted(
        articles,
        key=lambda a: (
            _IMPACT_RANK.get(str(a.get("industry_impact") or "Low"), 1),
            _IMPACT_RANK.get(str(a.get("telecom_relevance") or "Low"), 1),
            str(a.get("published_at") or ""),
        ),
        reverse=True,
    )

    clusters: list[list[dict[str, Any]]] = []
    for art in ranked:
        h_norm = (art.get("english_headline") or art.get("headline") or "").lower()
        h_tokens = _headline_tokens(h_norm)
        art_cat = art.get("main_category")
        art_ops = set(art.get("operators_mentioned") or [])

        placed = False
        for cluster in clusters:
            lead = cluster[0]
            lead_h = (lead.get("english_headline") or lead.get("headline") or "").lower()
            lead_tokens = _headline_tokens(lead_h)
            seq_sim = SequenceMatcher(None, h_norm, lead_h).ratio()
            overlap = len(h_tokens & lead_tokens)
            union = len(h_tokens | lead_tokens) or 1
            jaccard = overlap / union
            same_cat_and_op = (
                art_cat == lead.get("main_category")
                and bool(art_ops & set(lead.get("operators_mentioned") or []))
                and overlap >= 2
            )
            if seq_sim >= 0.62 or jaccard >= 0.42 or same_cat_and_op:
                cluster.append(art)
                placed = True
                break
        if not placed:
            clusters.append([art])

    # Prioritize clusters by max impact, max relevance, and multi-source corroboration
    clusters.sort(
        key=lambda cl: (
            max(_IMPACT_RANK.get(str(a.get("industry_impact") or "Low"), 1) for a in cl),
            max(_IMPACT_RANK.get(str(a.get("telecom_relevance") or "Low"), 1) for a in cl),
            len(cl),
        ),
        reverse=True,
    )
    return clusters[:max_clusters]


class DailyBriefingEngine:
    """Generates and caches grounded Daily Briefs from AI-analyzed articles."""

    def __init__(
        self,
        db_path: str | Path = "data/bd_telecom_news.db",
        *,
        config: LLMConfig | None = None,
        client: LocalOllamaClient | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.config = config or load_llm_config()
        self.repo = IntelligenceRepository(db_path)
        self.client = client or LocalOllamaClient(self.config)

    def _fetch_completed_articles(
        self,
        *,
        target_date: str | None = None,
        fallback_window_days: int = 7,
    ) -> tuple[list[dict[str, Any]], str, str]:
        """Load AI-completed articles for `target_date` or the latest active window."""
        conn = open_sqlite_connection(self.db_path, timeout=20.0)
        try:
            date_str = target_date or datetime.now(DHAKA_TZ).strftime("%Y-%m-%d")
            base_sql = """
                SELECT a.id, a.publisher, a.url, a.headline, a.language, a.published_at,
                       ai.english_headline, ai.summary, ai.key_takeaways_json,
                       ai.main_category, ai.subcategory, ai.organizations_json,
                       ai.operators_json, ai.sentiment, ai.industry_impact,
                       ai.telecom_relevance, ai.reported_facts_json, ai.industry_implications
                FROM articles a
                JOIN article_ai_analysis ai ON ai.article_id = a.id
                WHERE ai.status = 'COMPLETED'
                  AND ai.telecom_relevance IN ('High', 'Medium')
            """
            # 1. Try exact date first
            rows = conn.execute(
                base_sql + " AND substr(COALESCE(a.published_at, a.scraped_at), 1, 10) = ? ORDER BY a.published_at DESC",
                (date_str,),
            ).fetchall()
            period_label = date_str

            # 2. If exact date has < 2 articles, expand to rolling recent window so the briefing has substance
            if len(rows) < 2:
                end_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=DHAKA_TZ)
                start_str = (end_dt - timedelta(days=fallback_window_days)).strftime("%Y-%m-%d")
                rows = conn.execute(
                    base_sql
                    + " AND substr(COALESCE(a.published_at, a.scraped_at), 1, 10) >= ? "
                    + " AND substr(COALESCE(a.published_at, a.scraped_at), 1, 10) <= ? "
                    + " ORDER BY a.published_at DESC LIMIT 25",
                    (start_str, date_str),
                ).fetchall()
                period_label = f"{start_str} to {date_str}"

            # 3. If still < 2 (e.g. articles are slightly older), take latest completed batch
            if len(rows) < 2:
                rows = conn.execute(
                    base_sql + " ORDER BY COALESCE(a.published_at, a.scraped_at) DESC LIMIT 20"
                ).fetchall()
                if rows:
                    dates = sorted(
                        {str(r["published_at"] or "")[:10] for r in rows if r["published_at"]}
                    )
                    period_label = f"{dates[0]} to {dates[-1]}" if len(dates) >= 2 else (dates[0] if dates else date_str)
        finally:
            conn.close()

        items: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["key_takeaways"] = json.loads(d["key_takeaways_json"]) if d["key_takeaways_json"] else []
            d["organizations"] = json.loads(d["organizations_json"]) if d["organizations_json"] else []
            d["operators_mentioned"] = json.loads(d["operators_json"]) if d["operators_json"] else []
            d["reported_facts"] = json.loads(d["reported_facts_json"]) if d["reported_facts_json"] else []
            items.append(d)
        return items, date_str, period_label

    def generate_briefing(
        self,
        *,
        target_date: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Generate (or return cached) executive Daily Brief for `target_date`."""
        if not self.repo.acquire_job_lock("daily_briefing"):
            existing = self.repo.get_latest_briefing()
            if existing:
                return existing
            return {"status": "LOCKED", "error_message": "Briefing generation already in progress."}

        try:
            articles, date_str, period_label = self._fetch_completed_articles(target_date=target_date)
            valid_ids = [int(a["id"]) for a in articles]
            fp_payload = f"{date_str}|{sorted(valid_ids)}|{self.config.model_name}|{self.config.briefing_prompt_version}"
            fingerprint = hashlib.sha256(fp_payload.encode("utf-8")).hexdigest()[:20]

            if not force:
                existing = self.repo.get_briefing(date_str)
                if existing and existing["source_fingerprint"] == fingerprint and existing["status"] == "COMPLETED":
                    logger.info("Reusing cached Daily Brief for %s (unchanged sources).", date_str)
                    return existing

            if not articles:
                self.repo.save_briefing(
                    briefing_date=date_str,
                    period_label=period_label,
                    status="INSUFFICIENT_DATA",
                    output=None,
                    source_article_ids=[],
                    source_fingerprint=fingerprint,
                    model_name=self.config.model_name,
                    prompt_version=self.config.briefing_prompt_version,
                    error_message="No AI-processed telecom articles available for briefing generation.",
                )
                return self.repo.get_briefing(date_str) or {"status": "INSUFFICIENT_DATA"}

            clusters = cluster_related_articles(articles, max_clusters=5)
            cluster_blocks: list[str] = []
            valid_id_set = set(valid_ids)

            for idx, cl in enumerate(clusters, start=1):
                ids_str = ", ".join(str(a["id"]) for a in cl)
                lines = [f"### Cluster {idx} (Article IDs: [{ids_str}])"]
                for a in cl[:3]:
                    ops = ", ".join(a["operators_mentioned"]) or "None"
                    orgs = ", ".join(a["organizations"][:6]) or "None"
                    facts = "; ".join(a["reported_facts"][:4]) or "None"
                    lines.append(
                        f"- [ID {a['id']}] ({a['publisher']}, {str(a['published_at'] or '')[:10]}, "
                        f"Impact: {a['industry_impact']}, Category: {a['main_category']})\n"
                        f"  Headline: {a['english_headline']}\n"
                        f"  Summary: {a['summary']}\n"
                        f"  Operators: {ops} | Organizations: {orgs}\n"
                        f"  Key Figures/Facts: {facts}"
                    )
                cluster_blocks.append("\n".join(lines))

            user_prompt = build_briefing_user_prompt(period_label, "\n\n".join(cluster_blocks))
            output, _elapsed_ms, _attempts = self.client.generate_structured(
                system_prompt=BRIEFING_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema_cls=DailyBriefingOutput,
            )

            # Strictly sanitize supporting_article_ids so zero hallucinated IDs can ever be stored
            for idx, dev in enumerate(output.developments):
                cleaned_ids = [aid for aid in dev.supporting_article_ids if aid in valid_id_set]
                if not cleaned_ids and idx < len(clusters):
                    cleaned_ids = [int(a["id"]) for a in clusters[idx]]
                dev.supporting_article_ids = cleaned_ids

            self.repo.save_briefing(
                briefing_date=date_str,
                period_label=period_label,
                status="COMPLETED",
                output=output,
                source_article_ids=valid_ids,
                source_fingerprint=fingerprint,
                model_name=self.config.model_name,
                prompt_version=self.config.briefing_prompt_version,
            )
            return self.repo.get_briefing(date_str) or {"status": "COMPLETED"}
        except Exception as exc:
            logger.exception("Daily briefing generation failed: %s", exc)
            date_str = target_date or datetime.now(DHAKA_TZ).strftime("%Y-%m-%d")
            self.repo.save_briefing(
                briefing_date=date_str,
                period_label=date_str,
                status="FAILED",
                output=None,
                source_article_ids=[],
                source_fingerprint="failed",
                model_name=self.config.model_name,
                prompt_version=self.config.briefing_prompt_version,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            return self.repo.get_briefing(date_str) or {"status": "FAILED", "error_message": str(exc)}
        finally:
            self.repo.release_job_lock("daily_briefing")
