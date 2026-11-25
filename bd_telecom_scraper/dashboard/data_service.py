"""Efficient, paginated SQLite data service for the Editorial Intelligence Dashboard.

Queries only what each section renders — avoids loading the full database into memory.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..discovery import DHAKA_TZ
from ..llm.repository import IntelligenceRepository
from ..storage import open_sqlite_connection


def _parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return [str(x) for x in val] if isinstance(val, list) else []
    except json.JSONDecodeError:
        return []


class DashboardDataService:
    """Read-optimized query layer backed directly by `data/bd_telecom_news.db`."""

    def __init__(self, db_path: str | Path = "data/bd_telecom_news.db") -> None:
        self.db_path = Path(db_path)
        # Ensure additive tables exist safely
        self.repo = IntelligenceRepository(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        return open_sqlite_connection(self.db_path, timeout=15.0)

    def get_filter_options(self) -> dict[str, Any]:
        """Return distinct publishers, categories, operators, and date bounds."""
        with self._connect() as conn:
            publishers = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT publisher FROM articles ORDER BY publisher ASC"
                ).fetchall()
            ]
            categories = [
                r[0]
                for r in conn.execute(
                    """
                    SELECT DISTINCT main_category FROM article_ai_analysis
                    WHERE status = 'COMPLETED' AND main_category IS NOT NULL
                    ORDER BY main_category ASC
                    """
                ).fetchall()
            ]
            op_rows = conn.execute(
                "SELECT operators_json FROM article_ai_analysis WHERE status = 'COMPLETED'"
            ).fetchall()
            dates = [
                r[0]
                for r in conn.execute(
                    """
                    SELECT DISTINCT substr(COALESCE(published_at, scraped_at), 1, 10) AS d
                    FROM articles WHERE d IS NOT NULL ORDER BY d DESC LIMIT 30
                    """
                ).fetchall()
            ]

        op_set: set[str] = set()
        for r in op_rows:
            op_set.update(_parse_json_list(r[0]))

        return {
            "publishers": publishers,
            "categories": categories,
            "operators": sorted(op_set),
            "dates": dates,
        }

    def get_lead_story(self) -> dict[str, Any] | None:
        """Return the single highest-significance recent AI-analyzed article."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT a.id, a.url, a.publisher, a.language, a.headline AS original_headline,
                       a.published_at, a.scraped_at, a.summary AS raw_summary,
                       ai.english_headline, ai.summary, ai.key_takeaways_json,
                       ai.main_category, ai.subcategory, ai.organizations_json,
                       ai.operators_json, ai.sentiment, ai.sentiment_confidence,
                       ai.industry_impact, ai.telecom_relevance,
                       ai.reported_facts_json, ai.industry_implications,
                       ai.model_name, ai.processed_at
                FROM articles a
                JOIN article_ai_analysis ai ON ai.article_id = a.id
                WHERE ai.status = 'COMPLETED'
                ORDER BY
                    CASE ai.industry_impact WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END DESC,
                    CASE ai.telecom_relevance WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END DESC,
                    COALESCE(a.published_at, a.scraped_at) DESC
                LIMIT 1
                """
            ).fetchone()
        return self._row_to_article(row) if row else None

    def get_intelligence_feed(
        self,
        *,
        date_filter: str | None = None,
        publisher: str | None = None,
        category: str | None = None,
        operator: str | None = None,
        search_query: str | None = None,
        exclude_id: int | None = None,
        only_analyzed: bool = False,
        limit: int = 30,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query filtered articles (joined with AI intelligence where available)."""
        clauses: list[str] = []
        params: list[Any] = []

        if only_analyzed:
            clauses.append("ai.status = 'COMPLETED'")
        if exclude_id is not None:
            clauses.append("a.id != ?")
            params.append(exclude_id)
        if date_filter and date_filter != "All Dates":
            clauses.append("substr(COALESCE(a.published_at, a.scraped_at), 1, 10) = ?")
            params.append(date_filter)
        if publisher and publisher != "All Publishers":
            clauses.append("a.publisher = ?")
            params.append(publisher)
        if category and category != "All Categories":
            clauses.append("ai.main_category = ?")
            params.append(category)
        if operator and operator != "All Operators":
            clauses.append("(ai.operators_json LIKE ? OR a.entities_json LIKE ?)")
            like_op = f'%"{operator}"%'
            params.extend([like_op, like_op])
        if search_query and search_query.strip():
            q = f"%{search_query.strip()}%"
            clauses.append(
                "(a.headline LIKE ? OR COALESCE(ai.english_headline, '') LIKE ? "
                "OR COALESCE(ai.summary, a.summary) LIKE ? OR COALESCE(ai.organizations_json, '') LIKE ?)"
            )
            params.extend([q, q, q, q])

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT a.id, a.url, a.publisher, a.language, a.headline AS original_headline,
                   a.published_at, a.scraped_at, a.summary AS raw_summary,
                   a.entities_json AS raw_entities_json, a.matched_topics_json,
                   ai.status AS ai_status, ai.english_headline, ai.summary,
                   ai.key_takeaways_json, ai.main_category, ai.subcategory,
                   ai.organizations_json, ai.operators_json, ai.sentiment,
                   ai.sentiment_confidence, ai.industry_impact, ai.telecom_relevance,
                   ai.reported_facts_json, ai.industry_implications,
                   ai.model_name, ai.processed_at
            FROM articles a
            LEFT JOIN article_ai_analysis ai ON ai.article_id = a.id
            {where_sql}
            ORDER BY
                CASE WHEN ai.status = 'COMPLETED' THEN 1 ELSE 0 END DESC,
                COALESCE(a.published_at, a.scraped_at) DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_article(r) for r in rows]

    def get_articles_by_ids(self, article_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Lookup article headlines, publishers, and URLs for Daily Brief citations."""
        if not article_ids:
            return {}
        placeholders = ",".join("?" for _ in article_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT a.id, a.publisher, a.url, a.published_at, a.headline,
                       COALESCE(ai.english_headline, a.headline) AS display_headline
                FROM articles a
                LEFT JOIN article_ai_analysis ai ON ai.article_id = a.id
                WHERE a.id IN ({placeholders})
                """,
                article_ids,
            ).fetchall()
        return {int(r["id"]): dict(r) for r in rows}

    def get_industry_signals(self, window_days: int = 7) -> dict[str, Any]:
        """Compute topic momentum (current period vs prior period), operator mentions, and regulatory highlights.

        Distinguishes frequency from emerging momentum by comparing current `window_days`
        against the immediately preceding `window_days` period.
        """
        with self._connect() as conn:
            max_date_row = conn.execute(
                "SELECT MAX(substr(COALESCE(published_at, scraped_at), 1, 10)) FROM articles"
            ).fetchone()
            if not max_date_row or not max_date_row[0]:
                return {
                    "period_label": "No data",
                    "topics": [],
                    "operators": [],
                    "regulatory_highlights": [],
                }

            anchor_dt = datetime.strptime(max_date_row[0], "%Y-%m-%d").replace(tzinfo=DHAKA_TZ)
            cur_start = (anchor_dt - timedelta(days=window_days - 1)).strftime("%Y-%m-%d")
            prev_end = (anchor_dt - timedelta(days=window_days)).strftime("%Y-%m-%d")
            prev_start = (anchor_dt - timedelta(days=window_days * 2 - 1)).strftime("%Y-%m-%d")
            anchor_str = anchor_dt.strftime("%Y-%m-%d")

            rows = conn.execute(
                """
                SELECT a.id, a.publisher, a.url, a.headline,
                       substr(COALESCE(a.published_at, a.scraped_at), 1, 10) AS pub_date,
                       a.entities_json AS raw_entities,
                       ai.status AS ai_status, ai.english_headline, ai.main_category,
                       ai.operators_json, ai.industry_impact
                FROM articles a
                LEFT JOIN article_ai_analysis ai ON ai.article_id = a.id
                WHERE substr(COALESCE(a.published_at, a.scraped_at), 1, 10) >= ?
                """,
                (prev_start,),
            ).fetchall()

        cur_topics: Counter[str] = Counter()
        prev_topics: Counter[str] = Counter()
        high_impact_topics: Counter[str] = Counter()
        cur_ops: Counter[str] = Counter()
        prev_ops: Counter[str] = Counter()
        reg_highlights: list[dict[str, Any]] = []

        target_ops = {"Grameenphone", "Robi", "Banglalink", "Teletalk", "BTCL", "BTRC"}

        for r in rows:
            d = str(r["pub_date"] or "")
            in_cur = cur_start <= d <= anchor_str
            in_prev = prev_start <= d <= prev_end

            cat = r["main_category"] if r["ai_status"] == "COMPLETED" and r["main_category"] else None
            if cat:
                if in_cur:
                    cur_topics[cat] += 1
                    if r["industry_impact"] == "High":
                        high_impact_topics[cat] += 1
                elif in_prev:
                    prev_topics[cat] += 1

            ops = (
                _parse_json_list(r["operators_json"])
                if r["ai_status"] == "COMPLETED"
                else [e for e in _parse_json_list(r["raw_entities"]) if e in target_ops]
            )
            for op in set(ops):
                if in_cur:
                    cur_ops[op] += 1
                elif in_prev:
                    prev_ops[op] += 1

            if in_cur and cat in ("Telecom Regulation & Policy", "Spectrum & Licensing"):
                reg_highlights.append({
                    "id": r["id"],
                    "headline": r["english_headline"] or r["headline"],
                    "publisher": r["publisher"],
                    "url": r["url"],
                    "date": d,
                    "impact": r["industry_impact"] or "Medium",
                })

        topic_signals: list[dict[str, Any]] = []
        for cat, cur_cnt in cur_topics.most_common(8):
            prev_cnt = prev_topics.get(cat, 0)
            delta = cur_cnt - prev_cnt
            if cur_cnt >= 2 and (prev_cnt == 0 or cur_cnt >= prev_cnt * 1.5):
                momentum = "Emerging"
            elif delta > 0:
                momentum = "Rising"
            elif delta < 0:
                momentum = "Cooling"
            else:
                momentum = "Steady"
            topic_signals.append({
                "topic": cat,
                "current_count": cur_cnt,
                "prior_count": prev_cnt,
                "high_impact_count": high_impact_topics.get(cat, 0),
                "momentum": momentum,
            })

        operator_signals: list[dict[str, Any]] = []
        for op, cur_cnt in cur_ops.most_common(8):
            prev_cnt = prev_ops.get(op, 0)
            operator_signals.append({
                "operator": op,
                "current_count": cur_cnt,
                "prior_count": prev_cnt,
                "delta": cur_cnt - prev_cnt,
            })

        reg_highlights.sort(key=lambda x: (1 if x["impact"] == "High" else 0, x["date"]), reverse=True)

        return {
            "period_label": f"{cur_start} – {anchor_str} vs. {prev_start} – {prev_end}",
            "topics": topic_signals,
            "operators": operator_signals,
            "regulatory_highlights": reg_highlights[:5],
        }

    @staticmethod
    def _row_to_article(row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        ai_done = (row["ai_status"] == "COMPLETED") if "ai_status" in keys else True
        return {
            "id": int(row["id"]),
            "url": row["url"],
            "publisher": row["publisher"],
            "language": row["language"],
            "original_headline": row["original_headline"],
            "display_headline": row["english_headline"] if (ai_done and row["english_headline"]) else row["original_headline"],
            "published_at": row["published_at"],
            "scraped_at": row["scraped_at"],
            "ai_completed": ai_done,
            "summary": row["summary"] if (ai_done and row["summary"]) else row["raw_summary"],
            "key_takeaways": _parse_json_list(row["key_takeaways_json"]) if ai_done else [],
            "main_category": row["main_category"] if ai_done else "Pending Analysis",
            "subcategory": row["subcategory"] if ai_done else None,
            "organizations": _parse_json_list(row["organizations_json"]) if ai_done else _parse_json_list(row["raw_entities_json"] if "raw_entities_json" in keys else None),
            "operators": _parse_json_list(row["operators_json"]) if ai_done else [],
            "sentiment": row["sentiment"] if ai_done else None,
            "sentiment_confidence": row["sentiment_confidence"] if ai_done else None,
            "industry_impact": row["industry_impact"] if ai_done else None,
            "telecom_relevance": row["telecom_relevance"] if ai_done else None,
            "reported_facts": _parse_json_list(row["reported_facts_json"]) if ai_done else [],
            "industry_implications": row["industry_implications"] if ai_done else None,
            "model_name": row["model_name"] if ai_done else None,
            "processed_at": row["processed_at"] if ai_done else None,
        }
