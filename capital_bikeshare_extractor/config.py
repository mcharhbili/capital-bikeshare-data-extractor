"""Loads and validates config.yaml, exposing a typed accessor object."""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED_KEYS = (
    "s3_list_url",
    "s3_object_url",
    "gbfs_station_information_url",
    "supported_output_formats",
    "backend_extensions",
    "column_renames",
)

DEFAULT_DATA_DIR = Path("data")
DEFAULT_MANIFEST_PATH = DEFAULT_DATA_DIR / "manifest.json"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "bikeshare.db"
DEFAULT_TEMP_DIR = Path(".temp")


@dataclass(frozen=True)
class Config:
    s3_list_url: str
    s3_object_url: str
    gbfs_station_information_url: str
    supported_output_formats: list[str]
    backend_extensions: list[str]
    column_renames: dict[str, str]


def _read_yaml_text() -> str:
    return (
        importlib.resources.files("capital_bikeshare_extractor")
        .joinpath("config.yaml")
        .read_text(encoding="utf-8")
    )


def load_config() -> Config:
    raw_text = _read_yaml_text()
    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"config.yaml is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("config.yaml must contain a top-level mapping.")

    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise ValueError(f"config.yaml is missing required key(s): {missing!r}")

    return Config(
        s3_list_url=data["s3_list_url"],
        s3_object_url=data["s3_object_url"],
        gbfs_station_information_url=data["gbfs_station_information_url"],
        supported_output_formats=list(data["supported_output_formats"]),
        backend_extensions=list(data["backend_extensions"]),
        column_renames={
            str(k).strip(): str(v).strip() for k, v in data["column_renames"].items()
        },
    )
