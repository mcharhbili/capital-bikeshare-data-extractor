"""Download/extract/normalize/load pipeline for a single period.

Loading is idempotent: rows for a period+source_key are deleted and
reinserted inside one transaction, so re-syncing an unchanged or updated
period never errors on conflict and never leaves partial data on failure.
"""

from __future__ import annotations

import shutil
import sqlite3
import zipfile
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm

from capital_bikeshare_extractor.config import Config
from capital_bikeshare_extractor.http import SESSION
from capital_bikeshare_extractor.normalize import normalize_dataframe
from capital_bikeshare_extractor.schema import RAW_TRIPS_COLUMNS


def download_zip(config: Config, key: str, temp_dir: Path, force: bool = False) -> Path:
    temp_dir.mkdir(parents=True, exist_ok=True)
    dest = temp_dir / key

    if dest.exists() and not force:
        return dest

    url = config.s3_object_url.format(key=key)
    with SESSION.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length", 0))
        with dest.open("wb") as f, tqdm(
            total=total, unit="B", unit_scale=True, desc=key, leave=False
        ) as bar:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                bar.update(len(chunk))

    return dest


def extract_csvs(zip_path: Path, extract_dir: Path, force: bool = False) -> list[Path]:
    if extract_dir.exists() and not force:
        existing = list(extract_dir.glob("*.csv"))
        if existing:
            return existing

    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.endswith(".csv") and "__MACOSX" not in name:
                zf.extract(name, extract_dir)

    return list(extract_dir.rglob("*.csv"))


def cleanup_period_temp(zip_path: Path, extract_dir: Path) -> None:
    """Remove a period's downloaded ZIP and extracted CSVs from .temp."""
    zip_path.unlink(missing_ok=True)
    shutil.rmtree(extract_dir, ignore_errors=True)


def cleanup_temp_dir(temp_dir: Path) -> None:
    """Remove the entire .temp directory tree, if present."""
    shutil.rmtree(temp_dir, ignore_errors=True)


def load_period(
    conn: sqlite3.Connection,
    config: Config,
    csv_paths: list[Path],
    period: str,
    source_key: str,
    synced_at: str,
) -> tuple[int, int]:
    """Normalize the given CSVs and idempotently load them into raw_trips
    for this period/source_key. Returns (rows_deleted, rows_inserted).

    Runs inside a single transaction: delete-then-insert either fully
    commits or fully rolls back, so a crash mid-load can't leave the
    period half-deleted.
    """
    frames = []
    for path in csv_paths:
        raw_df = pd.read_csv(path, low_memory=False)
        frames.append(
            normalize_dataframe(
                raw_df,
                config.column_renames,
                period=period,
                source_key=source_key,
                synced_at=synced_at,
            )
        )

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=RAW_TRIPS_COLUMNS
    )

    try:
        cur = conn.execute(
            "DELETE FROM raw_trips WHERE period = ? AND source_key = ?",
            (period, source_key),
        )
        rows_deleted = cur.rowcount

        combined.to_sql("raw_trips", conn, if_exists="append", index=False)
        rows_inserted = len(combined)

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return rows_deleted, rows_inserted
