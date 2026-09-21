"""argparse CLI. Subcommand handlers only parse args, call into core/manifest/
s3_discovery/stations, and format output -- no business logic lives here."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

from capital_bikeshare_extractor import core, manifest as manifest_mod, s3_discovery, stations
from capital_bikeshare_extractor.config import (
    DEFAULT_DB_PATH,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_TEMP_DIR,
    load_config,
)
from capital_bikeshare_extractor.ingest import download_zip, extract_csvs, load_period
from capital_bikeshare_extractor.schema import init_db, refresh_trips_enriched
from capital_bikeshare_extractor.validation import assert_output_format


def _to_plain(obj):
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain(v) for v in obj]
    return obj


def _format_output(result, fmt: str) -> str:
    plain = _to_plain(result)
    if fmt == "json":
        return json.dumps(plain, indent=2, default=str)
    if fmt == "dict":
        return repr(plain)
    # print: human-readable
    return json.dumps(plain, indent=2, default=str)


def _format_error_chain(exc: BaseException) -> str:
    lines = [str(exc)]
    cause = exc.__cause__
    while cause is not None:
        lines.append(f"caused by: {cause}")
        cause = cause.__cause__
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cbse", description="Capital Bikeshare data extractor")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to the SQLite database.")
    parser.add_argument(
        "--manifest", default=str(DEFAULT_MANIFEST_PATH), help="Path to the JSON manifest."
    )
    parser.add_argument(
        "--output-format",
        default="print",
        choices=["print", "dict", "json"],
        help="How to render command output.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug-level logging."
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-files", help="List and classify S3 trip-data files.")

    manifest_parser = sub.add_parser("manifest", help="Inspect the sync manifest.")
    manifest_sub = manifest_parser.add_subparsers(dest="manifest_command", required=True)
    manifest_sub.add_parser("show", help="Show the current manifest contents.")
    manifest_sub.add_parser("diff", help="Diff S3 against the manifest without saving.")

    sub.add_parser("periods", help="Show all periods discovered on S3.")

    plan_parser = sub.add_parser("plan", help="Preview what sync-pending would do.")
    plan_parser.add_argument("--latest-only", action="store_true")

    init_db_parser = sub.add_parser("init-db", help="Create the database schema.")
    init_db_parser.add_argument("--force", action="store_true", help="Drop and recreate tables.")

    download_parser = sub.add_parser("download", help="Download a period's ZIP archive.")
    download_parser.add_argument("period")
    download_parser.add_argument("--force", action="store_true")

    extract_parser = sub.add_parser("extract", help="Extract a period's downloaded ZIP archive.")
    extract_parser.add_argument("period")
    extract_parser.add_argument("--force", action="store_true")

    process_parser = sub.add_parser(
        "process", help="Normalize and load an already-extracted period into SQLite."
    )
    process_parser.add_argument("period")
    process_parser.add_argument("--force", action="store_true")

    sync_period_parser = sub.add_parser("sync-period", help="Sync a single period.")
    sync_period_parser.add_argument("period")
    sync_period_parser.add_argument("--force", action="store_true")
    sync_period_parser.add_argument("--dry-run", action="store_true")

    sync_range_parser = sub.add_parser("sync-range", help="Sync a range of periods.")
    sync_range_parser.add_argument("start")
    sync_range_parser.add_argument("end")
    sync_range_parser.add_argument("--force", action="store_true")
    sync_range_parser.add_argument("--dry-run", action="store_true")

    sync_pending_parser = sub.add_parser("sync-pending", help="Sync all pending periods.")
    sync_pending_parser.add_argument("--latest-only", action="store_true")
    sync_pending_parser.add_argument("--force", action="store_true")
    sync_pending_parser.add_argument("--dry-run", action="store_true")

    sync_stations_parser = sub.add_parser("sync-stations", help="Sync live GBFS station data.")
    sync_stations_parser.add_argument("--dry-run", action="store_true")

    return parser


def _handle(args: argparse.Namespace) -> object:
    config = load_config()
    assert_output_format(args.output_format, config.supported_output_formats)
    db_path = Path(args.db)
    manifest_path = Path(args.manifest)

    if args.command == "list-files":
        periods, unclassified = s3_discovery.discover_periods(config)
        return {
            "periods": sorted(periods.keys()),
            "unclassified_keys": unclassified,
        }

    if args.command == "manifest":
        if args.manifest_command == "show":
            data = manifest_mod.load(manifest_path)
            return data.to_dict()
        if args.manifest_command == "diff":
            periods, _unclassified = s3_discovery.discover_periods(config)
            data = manifest_mod.load(manifest_path)
            result = manifest_mod.diff(periods, data)
            return {
                "new": result.new,
                "updated": result.updated,
                "completed": result.completed,
            }

    if args.command == "periods":
        periods = core.compute_periods(config)
        return sorted(periods.keys())

    if args.command == "plan":
        pending = core.plan_sync(config, manifest_path, latest_only=args.latest_only)
        return {"pending": pending}

    if args.command == "init-db":
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if args.force and db_path.exists():
            db_path.unlink()
        conn = sqlite3.connect(db_path)
        try:
            init_db(conn)
        finally:
            conn.close()
        return {"db": str(db_path), "status": "initialized"}

    if args.command in ("download", "extract", "process"):
        data = manifest_mod.load(manifest_path)
        entry = data.files.get(args.period)
        if entry is None:
            data, _result = core.refresh_manifest(config, manifest_path)
            entry = data.files.get(args.period)
            if entry is None:
                raise KeyError(f"Period {args.period!r} not found in S3 listing.")

        if args.command == "download":
            zip_path = download_zip(config, entry.key, DEFAULT_TEMP_DIR, force=args.force)
            return {"period": args.period, "zip_path": str(zip_path)}

        if args.command == "extract":
            zip_path = DEFAULT_TEMP_DIR / entry.key
            temp_dir = DEFAULT_TEMP_DIR / args.period
            csv_paths = extract_csvs(zip_path, temp_dir, force=args.force)
            return {"period": args.period, "csv_paths": [str(p) for p in csv_paths]}

        if args.command == "process":
            temp_dir = DEFAULT_TEMP_DIR / args.period
            csv_paths = list(temp_dir.rglob("*.csv"))
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(db_path)
            try:
                init_db(conn)
                synced_at = core.now_iso()
                rows_deleted, rows_inserted = load_period(
                    conn, config, csv_paths, args.period, entry.key, synced_at
                )
                refresh_trips_enriched(conn)
            finally:
                conn.close()
            manifest_mod.mark_completed(data, args.period, synced_at)
            manifest_mod.save(manifest_path, data, core.now_iso())
            return {
                "period": args.period,
                "rows_deleted": rows_deleted,
                "rows_inserted": rows_inserted,
            }

    if args.command == "sync-period":
        result = core.sync_period(
            config, db_path, manifest_path, args.period, force=args.force, dry_run=args.dry_run
        )
        return result

    if args.command == "sync-range":
        results = core.sync_range(
            config,
            db_path,
            manifest_path,
            args.start,
            args.end,
            force=args.force,
            dry_run=args.dry_run,
        )
        return results

    if args.command == "sync-pending":
        results = core.sync_pending(
            config,
            db_path,
            manifest_path,
            latest_only=args.latest_only,
            force=args.force,
            dry_run=args.dry_run,
        )
        return results

    if args.command == "sync-stations":
        if args.dry_run:
            return {"status": "skipped_dry_run"}
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            init_db(conn)
            synced_at = core.now_iso()
            count = stations.sync_stations(config, conn, synced_at)
            refresh_trips_enriched(conn)
        finally:
            conn.close()
        return {"status": "Completed", "stations_synced": count}

    raise ValueError(f"Unhandled command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        result = _handle(args)
    except Exception as exc:
        print(_format_error_chain(exc), file=sys.stderr)
        return 1

    print(_format_output(result, args.output_format))
    return 0


if __name__ == "__main__":
    sys.exit(main())
