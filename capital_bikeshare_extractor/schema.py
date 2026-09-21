"""DDL for raw_trips and stations."""

from __future__ import annotations

import sqlite3

RAW_TRIPS_COLUMNS = [
    "ride_id",
    "rideable_type",
    "started_at",
    "ended_at",
    "start_station_id",
    "start_station_name",
    "end_station_id",
    "end_station_name",
    "start_lat",
    "start_lng",
    "end_lat",
    "end_lng",
    "member_casual",
    "duration",
    "period",
    "source_key",
    "synced_at",
]

_CREATE_RAW_TRIPS = """
CREATE TABLE IF NOT EXISTS raw_trips (
    row_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ride_id             TEXT,
    rideable_type       TEXT,
    started_at          TEXT NOT NULL,
    ended_at            TEXT NOT NULL,
    start_station_id    TEXT,
    start_station_name  TEXT,
    end_station_id      TEXT,
    end_station_name    TEXT,
    start_lat           REAL,
    start_lng           REAL,
    end_lat             REAL,
    end_lng             REAL,
    member_casual       TEXT,
    duration             REAL,
    period               TEXT NOT NULL,
    source_key           TEXT NOT NULL,
    synced_at            TEXT NOT NULL
);
"""

_CREATE_RAW_TRIPS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_raw_trips_period ON raw_trips(period);",
    "CREATE INDEX IF NOT EXISTS idx_raw_trips_ride_id ON raw_trips(ride_id);",
    "CREATE INDEX IF NOT EXISTS idx_raw_trips_start_station ON raw_trips(start_station_id);",
    "CREATE INDEX IF NOT EXISTS idx_raw_trips_end_station ON raw_trips(end_station_id);",
]

_CREATE_STATIONS = """
CREATE TABLE IF NOT EXISTS stations (
    station_id   TEXT PRIMARY KEY,
    short_name   TEXT,
    name         TEXT NOT NULL,
    lat          REAL,
    lon          REAL,
    capacity     INTEGER,
    updated_at   TEXT NOT NULL
);
"""

_CREATE_STATIONS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_stations_short_name ON stations(short_name);",
]


def init_db(conn: sqlite3.Connection) -> None:
    """Create raw_trips and stations if they don't already exist. Safe to
    call repeatedly (idempotent)."""
    conn.execute(_CREATE_RAW_TRIPS)
    for stmt in _CREATE_RAW_TRIPS_INDEXES:
        conn.execute(stmt)
    conn.execute(_CREATE_STATIONS)
    for stmt in _CREATE_STATIONS_INDEXES:
        conn.execute(stmt)
    conn.commit()
