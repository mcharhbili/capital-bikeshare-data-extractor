"""Tracks per-period sync state in a JSON manifest.

The manifest is the source of truth for what's been synced. Diffing a fresh
S3 listing against it determines which periods are new/updated/pending, so
re-runs only touch what's changed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATUS_NEW = "new"
STATUS_UPDATED = "updated"
STATUS_COMPLETED = "Completed"

SCHEMA_VERSION = 1


@dataclass
class ManifestEntry:
    key: str
    period: str
    size: int
    status: str
    last_synced_at: str | None = None
    etag: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "period": self.period,
            "size": self.size,
            "status": self.status,
            "last_synced_at": self.last_synced_at,
            "etag": self.etag,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ManifestEntry":
        return ManifestEntry(
            key=data["key"],
            period=data["period"],
            size=data["size"],
            status=data["status"],
            last_synced_at=data.get("last_synced_at"),
            etag=data.get("etag", ""),
        )


@dataclass
class ManifestData:
    schema_version: int = SCHEMA_VERSION
    updated_at: str | None = None
    files: dict[str, ManifestEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "updated_at": self.updated_at,
            "files": {period: entry.to_dict() for period, entry in self.files.items()},
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ManifestData":
        return ManifestData(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            updated_at=data.get("updated_at"),
            files={
                period: ManifestEntry.from_dict(entry)
                for period, entry in data.get("files", {}).items()
            },
        )


@dataclass(frozen=True)
class DiffResult:
    new: list[str]
    updated: list[str]
    completed: list[str]

    @property
    def pending(self) -> list[str]:
        return [*self.new, *self.updated]


def load(path: Path) -> ManifestData:
    if not path.exists():
        return ManifestData()
    with path.open("r", encoding="utf-8") as f:
        return ManifestData.from_dict(json.load(f))


def save(path: Path, data: ManifestData, now: str) -> None:
    """Write the manifest atomically (temp file + os.replace)."""
    data.updated_at = now
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data.to_dict(), f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def diff(fresh_listing: dict[str, Any], existing: ManifestData) -> DiffResult:
    """Compare a fresh S3 listing (period -> PeriodInfo-like with .key/.size/.etag)
    against the existing manifest, returning new/updated/completed period lists.

    Periods present only in `existing` (removed from S3) are left out of the
    diff entirely -- local data for them is never deleted based on this diff.
    """
    new: list[str] = []
    updated: list[str] = []
    completed: list[str] = []

    for period, info in fresh_listing.items():
        prior = existing.files.get(period)

        if prior is None:
            new.append(period)
            continue

        changed = prior.etag != info.etag or prior.size != info.size
        if changed:
            updated.append(period)
        elif prior.status == STATUS_COMPLETED:
            completed.append(period)
        else:
            # Unchanged since last listing, but never finished syncing.
            if prior.status == STATUS_UPDATED:
                updated.append(period)
            else:
                new.append(period)

    return DiffResult(new=new, updated=updated, completed=completed)


def sync_from_listing(fresh_listing: dict[str, Any], existing: ManifestData) -> ManifestData:
    """Refresh `existing` in place with the latest S3 listing info (key/size/etag),
    assigning statuses per `diff()`, and return it."""
    result = diff(fresh_listing, existing)
    status_by_period = {
        **{p: STATUS_NEW for p in result.new},
        **{p: STATUS_UPDATED for p in result.updated},
        **{p: STATUS_COMPLETED for p in result.completed},
    }

    for period, info in fresh_listing.items():
        prior = existing.files.get(period)
        existing.files[period] = ManifestEntry(
            key=info.key,
            period=period,
            size=info.size,
            status=status_by_period[period],
            last_synced_at=prior.last_synced_at if prior else None,
            etag=info.etag,
        )

    return existing


def mark_completed(data: ManifestData, period: str, synced_at: str) -> None:
    entry = data.files.get(period)
    if entry is None:
        raise KeyError(f"Cannot mark unknown period {period!r} as completed.")
    entry.status = STATUS_COMPLETED
    entry.last_synced_at = synced_at
