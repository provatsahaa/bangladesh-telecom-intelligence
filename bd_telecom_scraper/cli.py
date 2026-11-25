"""Command-line interface for the Bangladesh Telecom News Scraper."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .pipeline import TelecomNewsScraper
from .sources import DEFAULT_SOURCES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bd_telecom_scraper",
        description="Collect Bangladesh telecom news from the last N days across leading portals.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Lookback window in days (default: 14).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/bd_telecom_news.db"),
        help="Path to SQLite database file (default: data/bd_telecom_news.db).",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        default=None,
        help="Optional subset of source_ids to scrape (use --list-sources to see all).",
    )
    parser.add_argument(
        "--max-per-source",
        type=int,
        default=40,
        help="Maximum candidate article pages to fetch per portal (default: 40).",
    )
    parser.add_argument(
        "--export-json",
        type=Path,
        default=None,
        help="Optional path to export stored articles as JSON.",
    )
    parser.add_argument(
        "--export-csv",
        type=Path,
        default=None,
        help="Optional path to export stored articles as CSV.",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="List all configured Bangladeshi news sources and exit.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.list_sources:
        for s in DEFAULT_SOURCES:
            print(f"{s.source_id:<20} | {s.language} | {s.publisher:<28} | {s.notes}")
        return 0

    scraper = TelecomNewsScraper(
        db_path=args.db,
        lookback_days=args.days,
        max_article_fetches_per_source=args.max_per_source,
    )
    report = scraper.run(source_ids=args.sources)

    print("\n=== Scrape Run Summary ===")
    print(
        f"Run ID: {report.run_id} | Window: last {report.lookback_days} days | "
        f"Discovered: {report.total_discovered} | Relevant: {report.total_relevant} | "
        f"Saved: {report.total_saved} | Duplicates: {report.total_duplicates}"
    )
    print("\nPer-Source Health:")
    for sh in report.source_health:
        methods = ",".join(sh.methods_used) if sh.methods_used else "-"
        print(
            f"  - {sh.source_id:<18} | {sh.status:<10} | methods={methods:<20} | "
            f"disc={sh.discovered_count:<4} rel={sh.relevant_count:<3} saved={sh.saved_count:<3} dup={sh.duplicate_count:<3}"
        )

    if args.export_json:
        count = scraper.store.export_json(args.export_json)
        print(f"\nExported {count} articles to JSON: {args.export_json}")
    if args.export_csv:
        count = scraper.store.export_csv(args.export_csv)
        print(f"Exported {count} articles to CSV: {args.export_csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
