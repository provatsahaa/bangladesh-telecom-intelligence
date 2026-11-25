"""CLI for the compliant Facebook Page telecom intelligence module."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .pipeline import FacebookIntelPipeline
from .source_registry import DEFAULT_FACEBOOK_SOURCES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bd_telecom_scraper.facebook",
        description="Compliant Facebook Page telecom intelligence module (Graph API v27.0 + Manual Ingestion).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/bd_telecom_news.db"),
        help="Path to shared SQLite database (default: data/bd_telecom_news.db).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-sources", help="List all 24 configured Facebook Page sources and verification statuses.")

    p_sync = sub.add_parser("sync", help="Run compliant Meta Graph API v27.0 sync for API-enabled Pages.")
    p_sync.add_argument("--days", type=int, default=14, help="Initial lookback window in days (default: 14).")
    p_sync.add_argument("--sources", nargs="+", default=None, help="Optional subset of source_ids.")

    p_manual = sub.add_parser("ingest-manual", help="Manually submit a single public Facebook post URL + text.")
    p_manual.add_argument("--url", required=True, help="Public Facebook post URL.")
    p_manual.add_argument("--text", required=True, help="Pasted public post text.")
    p_manual.add_argument("--page-name", default=None, help="Optional Facebook Page name (auto-matched if verified).")
    p_manual.add_argument("--published-at", default=None, help="Optional ISO-8601 publication timestamp.")
    p_manual.add_argument("--allow-low-relevance", action="store_true", help="Store even if below telecom threshold.")

    p_batch = sub.add_parser("ingest-batch", help="Ingest a batch of manual public posts from JSON or CSV.")
    p_batch.add_argument("--file", type=Path, required=True, help="Path to JSON or CSV file.")
    p_batch.add_argument("--allow-low-relevance", action="store_true", help="Store even if below telecom threshold.")

    p_exp = sub.add_parser("export", help="Export collected Facebook posts to JSON and/or CSV.")
    p_exp.add_argument("--json", type=Path, default=None, help="Output JSON path.")
    p_exp.add_argument("--csv", type=Path, default=None, help="Output CSV path.")

    p_purge = sub.add_parser("purge", help="Delete stored Facebook posts for data-retention/deletion compliance.")
    p_purge.add_argument("--source-id", default=None, help="Delete all posts from a specific source_id.")
    p_purge.add_argument("--older-than-days", type=int, default=None, help="Delete posts older than N days.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.command == "list-sources":
        for s in DEFAULT_FACEBOOK_SOURCES:
            url = s.facebook_url or "(unverified)"
            print(
                f"{s.source_id:<22} | {s.category:<13} | {s.verification_status:<29} | "
                f"{s.access_mode:<11} | {url}"
            )
        return 0

    pipeline = FacebookIntelPipeline(db_path=args.db, lookback_days=getattr(args, "days", 14))

    if args.command == "sync":
        report = pipeline.sync_via_graph_api(source_ids=args.sources)
        print(
            f"Run {report.run_id}: checked={report.sources_checked}, fetched={report.posts_fetched}, "
            f"relevant={report.posts_relevant}, saved={report.posts_saved}, dup={report.posts_duplicate}"
        )
        for sid, status in report.source_statuses.items():
            print(f"  - {sid:<22} | {status}")
        return 0

    if args.command == "ingest-manual":
        saved, post, reason = pipeline.ingest_manual_post(
            post_url=args.url,
            post_text=args.text,
            page_name=args.page_name,
            published_at=args.published_at,
            require_relevant=not args.allow_low_relevance,
        )
        print(f"Status: {reason} | saved={saved}")
        if post:
            print(
                f"Page: {post.page_name} | Lang: {post.language} | Relevance: "
                f"{post.relevance_classification} ({post.relevance_score}) | Entities: {post.entities}"
            )
        return 0

    if args.command == "ingest-batch":
        report = pipeline.ingest_manual_batch(
            args.file, require_relevant=not args.allow_low_relevance
        )
        print(
            f"Batch {report.run_id}: fetched={report.posts_fetched}, relevant={report.posts_relevant}, "
            f"saved={report.posts_saved}, dup={report.posts_duplicate}"
        )
        return 0

    if args.command == "export":
        if args.json:
            n = pipeline.store.export_json(args.json)
            print(f"Exported {n} Facebook posts to {args.json}")
        if args.csv:
            n = pipeline.store.export_csv(args.csv)
            print(f"Exported {n} Facebook posts to {args.csv}")
        return 0

    if args.command == "purge":
        if args.source_id:
            n = pipeline.store.purge_source(args.source_id)
            print(f"Purged {n} posts for source {args.source_id}")
        if args.older_than_days is not None:
            n = pipeline.store.purge_older_than(args.older_than_days)
            print(f"Purged {n} posts older than {args.older_than_days} days")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
