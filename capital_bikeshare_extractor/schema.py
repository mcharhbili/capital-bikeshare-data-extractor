"""DDL for raw_trips, stations, and the trips_enriched dedup/enrichment view."""

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

_CREATE_TRIPS_ENRICHED_VIEW = """
CREATE VIEW IF NOT EXISTS trips_enriched AS
WITH deduped AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY ride_id
               ORDER BY synced_at DESC, row_id DESC
           ) AS rn
    FROM raw_trips
    WHERE ride_id IS NOT NULL

    UNION ALL

    SELECT *, 1 AS rn
    FROM raw_trips
    WHERE ride_id IS NULL
)
SELECT
    d.row_id,
    d.ride_id,
    d.rideable_type,
    d.started_at,
    d.ended_at,
    d.duration,
    d.member_casual,
    d.period,
    d.source_key,
    d.synced_at,
    d.start_station_id,
    COALESCE(ss.name, d.start_station_name) AS start_station_name,
    COALESCE(ss.lat,  d.start_lat)          AS start_lat,
    COALESCE(ss.lon,  d.start_lng)          AS start_lng,
    ss.capacity                             AS start_station_capacity,
    d.end_station_id,
    COALESCE(es.name, d.end_station_name)   AS end_station_name,
    COALESCE(es.lat,  d.end_lat)            AS end_lat,
    COALESCE(es.lon,  d.end_lng)            AS end_lng,
    es.capacity                             AS end_station_capacity
FROM deduped d
LEFT JOIN stations ss ON ss.station_id = d.start_station_id OR ss.short_name = d.start_station_id
LEFT JOIN stations es ON es.station_id = d.end_station_id OR es.short_name = d.end_station_id
WHERE d.rn = 1;
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create raw_trips, stations, and the trips_enriched view if they don't
    already exist. Safe to call repeatedly (idempotent)."""
    conn.execute(_CREATE_RAW_TRIPS)
    for stmt in _CREATE_RAW_TRIPS_INDEXES:
        conn.execute(stmt)
    conn.execute(_CREATE_STATIONS)
    for stmt in _CREATE_STATIONS_INDEXES:
        conn.execute(stmt)
    conn.execute(_CREATE_TRIPS_ENRICHED_VIEW)
    conn.commit()
