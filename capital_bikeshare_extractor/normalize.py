"""Normalizes source CSV DataFrames to the canonical raw_trips schema.

Column names vary across historical file eras (e.g. 'Start date' vs
'started_at'). Recognized headers are remapped via a config-driven table;
any unrecognized header raises loudly rather than being silently dropped,
since that would mean quietly losing data.
"""

from __future__ import annotations

import pandas as pd

from capital_bikeshare_extractor.schema import RAW_TRIPS_COLUMNS
from capital_bikeshare_extractor.validation import UnrecognizedColumnsError

_DATE_COLUMNS = ("started_at", "ended_at")
_NUMERIC_COLUMNS = ("start_lat", "start_lng", "end_lat", "end_lng", "duration")


def normalize_dataframe(
    df: pd.DataFrame,
    column_renames: dict[str, str],
    period: str,
    source_key: str,
    synced_at: str,
) -> pd.DataFrame:
    """Remap and validate a raw source DataFrame into the canonical
    raw_trips column set, ready for `to_sql`.

    Raises UnrecognizedColumnsError if any source column has no mapping.
    """
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    unrecognized = {c for c in df.columns if c not in column_renames}
    if unrecognized:
        raise UnrecognizedColumnsError(unrecognized, source_key)

    df = df.rename(columns=column_renames)

    # Multiple source headers can map to the same canonical name across
    # different file eras; a single file should never produce duplicate
    # canonical columns, but guard defensively by keeping the first.
    df = df.loc[:, ~df.columns.duplicated()]

    for col in RAW_TRIPS_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    for col in _DATE_COLUMNS:
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%dT%H:%M:%S")

    for col in _NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["period"] = period
    df["source_key"] = source_key
    df["synced_at"] = synced_at

    return df[RAW_TRIPS_COLUMNS]
