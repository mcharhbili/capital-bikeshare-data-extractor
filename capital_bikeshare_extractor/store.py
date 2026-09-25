"""Parquet storage backend: raw_trips partitioned by year/month, stations as
a single full-refresh file. Exposes the Store interface core.py/cli.py use.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pandas as pd

from capital_bikeshare_extractor.config import Config
from capital_bikeshare_extractor.normalize import normalize_dataframe
from capital_bikeshare_extractor.schema import RAW_TRIPS_COLUMNS
from capital_bikeshare_extractor.stations import fetch_station_information

STATIONS_COLUMNS = [
    "station_id",
    "short_name",
    "name",
    "lat",
    "lon",
    "capacity",
    "updated_at",
]


class Store(Protocol):
    """Persistence interface for raw_trips and stations."""

    def init(self) -> None: ...

    def load_period(
        self,
        config: Config,
        csv_paths: list[Path],
        period: str,
        source_key: str,
        synced_at: str,
    ) -> tuple[int, int]: ...

    def sync_stations(self, config: Config, synced_at: str) -> int: ...


def _period_year_month(period: str) -> tuple[str, str]:
    """Split a 'YYYY' or 'YYYY-MM' period into (year, month), defaulting
    month to '00' for yearly (pre-2018) periods that have no month."""
    if "-" in period:
        year, month = period.split("-")
    else:
        year, month = period, "00"
    return year, month


class ParquetStore:
    """Writes raw_trips as one Parquet file per period, partitioned into
    year=YYYY/month=MM/ directories under `data_dir/raw_trips/`. Re-syncing
    a period overwrites its file wholesale, so this stays idempotent without
    append-time dedup.

    stations is a full-refresh upsert, kept as a single small file at
    `data_dir/stations.parquet` rather than partitioned.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.raw_trips_dir = self.data_dir / "raw_trips"
        self.stations_path = self.data_dir / "stations.parquet"

    def init(self) -> None:
        self.raw_trips_dir.mkdir(parents=True, exist_ok=True)

    def _period_path(self, period: str) -> Path:
        year, month = _period_year_month(period)
        return self.raw_trips_dir / f"year={year}" / f"month={month}" / f"period={period}.parquet"

    def load_period(
        self,
        config: Config,
        csv_paths: list[Path],
        period: str,
        source_key: str,
        synced_at: str,
    ) -> tuple[int, int]:
        self.init()

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

        combined = (
            pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=RAW_TRIPS_COLUMNS)
        )

        out_path = self._period_path(period)
        rows_deleted = 0
        if out_path.exists():
            rows_deleted = len(pd.read_parquet(out_path))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(".parquet.tmp")
        combined.to_parquet(tmp_path, index=False)
        tmp_path.replace(out_path)

        return rows_deleted, len(combined)

    def sync_stations(self, config: Config, synced_at: str) -> int:
        self.init()
        stations = fetch_station_information(config)
        df = pd.DataFrame(
            [
                {
                    "station_id": s.station_id,
                    "short_name": s.short_name,
                    "name": s.name,
                    "lat": s.lat,
                    "lon": s.lon,
                    "capacity": s.capacity,
                    "updated_at": synced_at,
                }
                for s in stations
            ],
            columns=STATIONS_COLUMNS,
        )

        tmp_path = self.stations_path.with_suffix(".parquet.tmp")
        df.to_parquet(tmp_path, index=False)
        tmp_path.replace(self.stations_path)

        return len(stations)


def make_store(data_dir: Path) -> Store:
    return ParquetStore(data_dir)
