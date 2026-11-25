"""CLI for Tavily Search & Extract discovery and credit-usage monitoring."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import load_tavily_config
from .pipeline import TavilyDiscoveryPipeline
from .queries import DEFAULT_TAVILY_QUERIES
from .usage import TavilyUsageTracker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bd_telecom_scraper.tavily",
        description="Tavily Search & Extract telecom news discovery with credit-usage monitoring.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/bd_telecom_news.db"),
        help="Path to shared SQLite database (default: data/bd_telecom_news.db).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("queries", help="List configured Bangladesh and global Tavily discovery queries.")
    sub.add_parser("usage", help="Show monthly Tavily credit consumption and remaining free-tier budget.")

    p_disc = sub.add_parser("discover", help="Run Tavily Search discovery with Extract fallback.")
    p_disc.add_argument("--days", type=int, default=14, help="Lookback window in days (default: 14).")
    p_disc.add_argument("--scope", choices=["bangladesh", "global"], default=None, help="Filter query scope.")
    p_disc.add_argument("--query-ids", nargs="+", default=None, help="Run specific query_ids only.")
    p_disc.add_argument("--max-queries", type=int, default=None, help="Cap number of queries executed.")
    p_disc.add_argument("--force", action="store_true", help="Bypass per-query cooldown interval.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    cfg = load_tavily_config()

    if args.command == "queries":
        for q in DEFAULT_TAVILY_QUERIES:
            print(f"{q.query_id:<32} | {q.scope:<10} | domains={len(q.include_domains):<2} | {q.query}")
        return 0

    if args.command == "usage":
        tracker = TavilyUsageTracker(args.db)
        print(json.dumps(tracker.get_usage_summary(monthly_budget=cfg.monthly_credit_budget), indent=2))
        return 0

    if args.command == "discover":
        pipeline = TavilyDiscoveryPipeline(db_path=args.db, config=cfg)
        rep = pipeline.run(
            days=args.days,
            query_ids=args.query_ids,
            scope=args.scope,
            max_queries=args.max_queries,
            force=args.force,
        )
        print("\n=== Tavily Discovery Run Summary ===")
        print(
            f"Queries executed: {rep.queries_executed} (cooldown skipped: {rep.queries_skipped_cooldown}, "
            f"budget blocked: {rep.queries_blocked_budget})"
        )
        print(
            f"Hits returned: {rep.hits_returned} | Already collected (skipped): {rep.skipped_already_collected} | "
            f"Primary extracted: {rep.extracted_primary} | Tavily Extract fallback: {rep.extracted_tavily_fallback} | "
            f"New articles saved: {rep.articles_saved}"
        )
        print(
            f"Credits used this run: {rep.credits_used_this_run} | "
            f"Monthly total: {rep.monthly_credits_used} / {rep.monthly_budget}"
        )
        if rep.errors:
            for e in rep.errors:
                print(f"  WARNING: {e}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
