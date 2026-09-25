"""Download/extract pipeline for a single period's ZIP archive. Normalizing
and loading the extracted CSVs is handled by the active store (see store.py).
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from tqdm import tqdm

from capital_bikeshare_extractor.config import Config
from capital_bikeshare_extractor.http import SESSION


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
