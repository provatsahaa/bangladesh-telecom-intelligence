"""CLI for the local Ollama LLM intelligence engine, benchmark, and daily briefing."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .benchmark import run_benchmark
from .briefing import DailyBriefingEngine
from .client import LocalOllamaClient
from .config import load_llm_config
from .pipeline import ArticleIntelligencePipeline
from .repository import IntelligenceRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bd_telecom_scraper.llm",
        description="Local Ollama LLM intelligence engine for Bangladesh Telecom Intelligence.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/bd_telecom_news.db"),
        help="Path to shared SQLite database (default: data/bd_telecom_news.db).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override Ollama model name (default: OLLAMA_MODEL env var or qwen2.5:3b).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show Ollama connection status and AI processing queue metrics.")

    p_bench = sub.add_parser("benchmark", help="Benchmark candidate Ollama models on Bangla + English sample.")
    p_bench.add_argument("--models", nargs="+", default=["qwen2.5:3b", "gemma3:1b"])
    p_bench.add_argument("--bn", type=int, default=3)
    p_bench.add_argument("--en", type=int, default=3)
    p_bench.add_argument("--out", type=Path, default=Path("data/benchmarks/llm_model_benchmark.json"))

    p_proc = sub.add_parser("process", help="Process pending/changed scraped articles.")
    p_proc.add_argument("--limit", type=int, default=10, help="Batch size (default: 10).")
    p_proc.add_argument("--ids", nargs="+", type=int, default=None, help="Explicit article IDs to process.")
    p_proc.add_argument("--force", action="store_true", help="Force reprocess explicit --ids.")

    p_brief = sub.add_parser("briefing", help="Generate or refresh The Daily Brief.")
    p_brief.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD (default: today).")
    p_brief.add_argument("--force", action="store_true", help="Force regeneration even if cached.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    cfg = load_llm_config(model_override=args.model)

    if args.command == "status":
        client = LocalOllamaClient(cfg)
        ok, models, err = client.check_health()
        repo = IntelligenceRepository(args.db)
        metrics = repo.get_queue_metrics()
        print(json.dumps({
            "ollama_connected": ok,
            "ollama_base_url": cfg.base_url,
            "configured_model": cfg.model_name,
            "installed_models": models,
            "ollama_error": err,
            "queue": metrics,
        }, indent=2))
        return 0

    if args.command == "benchmark":
        report = run_benchmark(
            models=args.models,
            db_path=args.db,
            output_path=args.out,
            bn_count=args.bn,
            en_count=args.en,
        )
        print(f"Benchmark saved to {args.out}")
        for m, stats in report["models"].items():
            print(
                f"  - {m:<14} | ok={stats['succeeded']}/{stats['total_sample']} | "
                f"1st_pass_json={stats['first_pass_json_ok']}/{stats['total_sample']} | "
                f"avg_ms={stats['avg_latency_ms']} | mem_delta_mb={stats['system_mem_delta_mb']}"
            )
        return 0

    if args.command == "process":
        pipeline = ArticleIntelligencePipeline(db_path=args.db, config=cfg)
        res = pipeline.process_batch(
            limit=args.limit,
            article_ids=args.ids,
            force_reprocess=args.force,
        )
        print(
            f"Processed (model={res.model_name}): attempted={res.attempted}, "
            f"completed={res.completed}, failed={res.failed}, locked={res.skipped_locked}"
        )
        if res.errors:
            for e in res.errors[:5]:
                print(f"  ERROR: {e}")
        return 0 if not res.ollama_unavailable else 1

    if args.command == "briefing":
        engine = DailyBriefingEngine(db_path=args.db, config=cfg)
        brief = engine.generate_briefing(target_date=args.date, force=args.force)
        print(json.dumps(brief, ensure_ascii=False, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
