"""High-level orchestration: sync_period, sync_range, sync_pending, plan_sync.

This is the layer cli.py calls into. It composes s3_discovery, manifest,
ingest, and schema, but contains no argparse or print logic, so it's
directly unit-testable.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dateutil.relativedelta import relativedelta

from capital_bikeshare_extractor import manifest as manifest_mod
from capital_bikeshare_extractor import s3_discovery
from capital_bikeshare_extractor.config import Config, DEFAULT_TEMP_DIR
from capital_bikeshare_extractor.ingest import (
    cleanup_period_temp,
    cleanup_temp_dir,
    download_zip,
    extract_csvs,
    load_period,
)
from capital_bikeshare_extractor.schema import init_db, refresh_trips_enriched
from capital_bikeshare_extractor.validation import assert_period, is_monthly_period

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


@dataclass(frozen=True)
class SyncResult:
    period: str
    status: str  # "Completed", "already_completed", "skipped_dry_run"
    rows_deleted: int = 0
    rows_inserted: int = 0


def compute_periods(config: Config) -> dict[str, s3_discovery.PeriodInfo]:
    periods, _unclassified = s3_discovery.discover_periods(config)
    return periods


def refresh_manifest(
    config: Config, manifest_path: Path
) -> tuple[manifest_mod.ManifestData, manifest_mod.DiffResult]:
    periods, _unclassified = s3_discovery.discover_periods(config)
    data = manifest_mod.load(manifest_path)
    result = manifest_mod.diff(periods, data)
    data = manifest_mod.sync_from_listing(periods, data)
    manifest_mod.save(manifest_path, data, now_iso())
    return data, result


def plan_sync(
    config: Config, manifest_path: Path, latest_only: bool = False
) -> list[str]:
    """Return the list of periods that sync_pending would process, without
    any side effects beyond refreshing the manifest's view of S3 state."""
    _data, result = refresh_manifest(config, manifest_path)
    pending = result.pending
    if latest_only and pending:
        pending = [sorted(pending)[-1]]
    return pending


def sync_period(
    config: Config,
    db_path: Path,
    manifest_path: Path,
    period: str,
    force: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    assert_period(period)

    data = manifest_mod.load(manifest_path)
    entry = data.files.get(period)
    if entry is None:
        # Manifest doesn't know about this period yet; refresh from S3.
        data, _result = refresh_manifest(config, manifest_path)
        entry = data.files.get(period)
        if entry is None:
            raise KeyError(f"Period {period!r} not found in S3 listing.")

    if entry.status == manifest_mod.STATUS_COMPLETED and not force:
        return SyncResult(period=period, status="already_completed")

    if dry_run:
        return SyncResult(period=period, status="skipped_dry_run")

    temp_dir = DEFAULT_TEMP_DIR / period
    zip_path = download_zip(config, entry.key, DEFAULT_TEMP_DIR, force=force)
    csv_paths = extract_csvs(zip_path, temp_dir, force=force)

    synced_at = now_iso()
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        rows_deleted, rows_inserted = load_period(
            conn, config, csv_paths, period, entry.key, synced_at
        )
        refresh_trips_enriched(conn)
    finally:
        conn.close()

    manifest_mod.mark_completed(data, period, synced_at)
    manifest_mod.save(manifest_path, data, now_iso())

    cleanup_period_temp(zip_path, temp_dir)

    return SyncResult(
        period=period,
        status="Completed",
        rows_deleted=rows_deleted,
        rows_inserted=rows_inserted,
    )


def _step_period(period: str) -> str:
    if is_monthly_period(period):
        year, month = period.split("-")
        dt = datetime(int(year), int(month), 1) + relativedelta(months=1)
        return f"{dt.year:04d}-{dt.month:02d}"
    else:
        return str(int(period) + 1)


def sync_range(
    config: Config,
    db_path: Path,
    manifest_path: Path,
    start: str,
    end: str,
    force: bool = False,
    dry_run: bool = False,
) -> list[SyncResult]:
    assert_period(start)
    assert_period(end)
    if is_monthly_period(start) != is_monthly_period(end):
        raise ValueError(
            f"start ({start!r}) and end ({end!r}) must both be yearly or both monthly periods."
        )

    results: list[SyncResult] = []
    period = start
    while True:
        result = sync_period(
            config, db_path, manifest_path, period, force=force, dry_run=dry_run
        )
        results.append(result)
        if result.status not in ("Completed", "already_completed", "skipped_dry_run"):
            break
        if period == end:
            break
        period = _step_period(period)

    if not dry_run:
        cleanup_temp_dir(DEFAULT_TEMP_DIR)

    return results


def sync_pending(
    config: Config,
    db_path: Path,
    manifest_path: Path,
    latest_only: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> list[SyncResult]:
    _data, result = refresh_manifest(config, manifest_path)
    pending = result.pending
    if latest_only and pending:
        pending = [sorted(pending)[-1]]

    if not pending:
        logger.info("sync-pending: no pending periods, nothing to do")
        return []

    logger.info("sync-pending: %d period(s) to sync: %s", len(pending), ", ".join(pending))

    results = []
    for i, period in enumerate(pending, start=1):
        logger.info("sync-pending: [%d/%d] syncing %s", i, len(pending), period)
        result = sync_period(config, db_path, manifest_path, period, force=force, dry_run=dry_run)
        logger.info(
            "sync-pending: [%d/%d] %s -> %s (rows_deleted=%d, rows_inserted=%d)",
            i,
            len(pending),
            period,
            result.status,
            result.rows_deleted,
            result.rows_inserted,
        )
        results.append(result)

    if not dry_run:
        cleanup_temp_dir(DEFAULT_TEMP_DIR)

    completed = sum(1 for r in results if r.status == "Completed")
    logger.info(
        "sync-pending: done, %d/%d period(s) completed", completed, len(results)
    )

    return results
