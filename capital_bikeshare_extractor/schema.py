"""Canonical raw_trips column list, shared by normalize.py and store.py."""

from __future__ import annotations

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
