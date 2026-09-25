"""Fetches the live GBFS station_information feed."""

from __future__ import annotations

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
