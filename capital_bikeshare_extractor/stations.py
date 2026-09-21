"""Fetches the live GBFS station_information feed and upserts it into stations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from capital_bikeshare_extractor.config import Config
from capital_bikeshare_extractor.http import SESSION


@dataclass(frozen=True)
class StationRecord:
    station_id: str
    short_name: str | None
    name: str
    lat: float | None
    lon: float | None
    capacity: int | None


def fetch_station_information(config: Config) -> list[StationRecord]:
    response = SESSION.get(config.gbfs_station_information_url, timeout=30)
    response.raise_for_status()
    payload = response.json()

    stations = []
    for raw in payload.get("data", {}).get("stations", []):
        stations.append(
            StationRecord(
                station_id=str(raw["station_id"]),
                short_name=str(raw["short_name"]) if raw.get("short_name") else None,
                name=raw.get("name", ""),
                lat=raw.get("lat"),
                lon=raw.get("lon"),
                capacity=raw.get("capacity"),
            )
        )
    return stations


def upsert_stations(
    conn: sqlite3.Connection, stations: list[StationRecord], synced_at: str
) -> int:
    conn.executemany(
        """
        INSERT INTO stations (station_id, short_name, name, lat, lon, capacity, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(station_id) DO UPDATE SET
            short_name = excluded.short_name,
            name = excluded.name,
            lat = excluded.lat,
            lon = excluded.lon,
            capacity = excluded.capacity,
            updated_at = excluded.updated_at
        """,
        [
            (s.station_id, s.short_name, s.name, s.lat, s.lon, s.capacity, synced_at)
            for s in stations
        ],
    )
    conn.commit()
    return len(stations)


def sync_stations(config: Config, conn: sqlite3.Connection, synced_at: str) -> int:
    stations = fetch_station_information(config)
    return upsert_stations(conn, stations, synced_at)
