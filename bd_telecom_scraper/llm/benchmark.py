"""Reproducible multi-model benchmark harness for 8 GB MacBook Air M1."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from ..discovery import DHAKA_TZ
from ..storage import open_sqlite_connection
from .client import LocalOllamaClient
from .config import load_llm_config
from .prompts import ARTICLE_SYSTEM_PROMPT, build_article_user_prompt
from .schemas import ArticleIntelligence


def select_stratified_sample(
    db_path: str | Path,
    *,
    bn_count: int = 3,
    en_count: int = 3,
) -> list[dict[str, Any]]:
    """Select a balanced Bangla + English sample from existing scraped articles."""
    conn = open_sqlite_connection(db_path, timeout=20.0)
    try:
        bn_rows = conn.execute(
            """
            SELECT * FROM articles
            WHERE language = 'bn' AND length(content) >= 200
            ORDER BY relevance_score DESC, published_at DESC
            LIMIT ?
            """,
            (bn_count,),
        ).fetchall()
        en_rows = conn.execute(
            """
            SELECT * FROM articles
            WHERE language = 'en' AND length(content) >= 200
            ORDER BY relevance_score DESC, published_at DESC
            LIMIT ?
            """,
            (en_count,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in list(bn_rows) + list(en_rows)]


def run_benchmark(
    *,
    models: list[str],
    db_path: str | Path = "data/bd_telecom_news.db",
    output_path: str | Path = "data/benchmarks/llm_model_benchmark.json",
    bn_count: int = 3,
    en_count: int = 3,
) -> dict[str, Any]:
    """Benchmark candidate Ollama models on the same stratified Bangla+English articles."""
    sample = select_stratified_sample(db_path, bn_count=bn_count, en_count=en_count)
    results_by_model: dict[str, Any] = {}

    for model_name in models:
        cfg = load_llm_config(model_override=model_name)
        client = LocalOllamaClient(cfg)
        records: list[dict[str, Any]] = []
        latencies: list[int] = []
        first_pass_ok = 0
        succeeded = 0
        mem_before_mb = psutil.virtual_memory().used / (1024 * 1024)
        peak_mem_mb = mem_before_mb

        for art in sample:
            user_prompt = build_article_user_prompt(
                publisher=str(art["publisher"]),
                language=str(art["language"]),
                published_at=art.get("published_at"),
                headline=str(art["headline"]),
                content=str(art["content"])[: cfg.max_input_chars],
            )
            t0 = time.monotonic()
            try:
                intel, elapsed_ms, attempts = client.generate_structured(
                    system_prompt=ARTICLE_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    schema_cls=ArticleIntelligence,
                    model_override=model_name,
                )
                succeeded += 1
                if attempts == 1:
                    first_pass_ok += 1
                latencies.append(elapsed_ms)
                peak_mem_mb = max(peak_mem_mb, psutil.virtual_memory().used / (1024 * 1024))
                records.append({
                    "article_id": art["id"],
                    "language": art["language"],
                    "publisher": art["publisher"],
                    "original_headline": art["headline"],
                    "ok": True,
                    "attempts": attempts,
                    "latency_ms": elapsed_ms,
                    "output": intel.model_dump(),
                })
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                records.append({
                    "article_id": art["id"],
                    "language": art["language"],
                    "publisher": art["publisher"],
                    "original_headline": art["headline"],
                    "ok": False,
                    "latency_ms": elapsed_ms,
                    "error": f"{type(exc).__name__}: {exc}",
                })

        avg_ms = int(sum(latencies) / len(latencies)) if latencies else 0
        results_by_model[model_name] = {
            "total_sample": len(sample),
            "succeeded": succeeded,
            "first_pass_json_ok": first_pass_ok,
            "avg_latency_ms": avg_ms,
            "system_mem_delta_mb": round(max(0.0, peak_mem_mb - mem_before_mb), 1),
            "records": records,
        }

    report = {
        "generated_at": datetime.now(DHAKA_TZ).isoformat(),
        "sample_article_ids": [a["id"] for a in sample],
        "models": results_by_model,
    }
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
