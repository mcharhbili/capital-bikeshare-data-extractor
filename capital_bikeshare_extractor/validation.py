"""Small, dependency-free validators shared across config/cli/normalize."""

from __future__ import annotations

import re

PERIOD_RE = re.compile(r"^\d{4}(-\d{2})?$")


class UnrecognizedColumnsError(Exception):
    """Raised when a source CSV contains columns absent from the configured
    column-rename map. Signals schema drift rather than silently dropping
    data."""

    def __init__(self, columns: set[str], source_key: str):
        self.columns = columns
        self.source_key = source_key
        super().__init__(
            f"Unrecognized column(s) {sorted(columns)!r} in {source_key!r}. "
            "Add a mapping for these headers to config.yaml's column_renames."
        )


def assert_period(period: str) -> str:
    """Validate a period string is 'YYYY' or 'YYYY-MM'. Returns it unchanged."""
    if not PERIOD_RE.match(period):
        raise ValueError(
            f"Invalid period {period!r}: expected 'YYYY' or 'YYYY-MM'."
        )
    return period


def assert_output_format(fmt: str, supported: list[str]) -> str:
    if fmt not in supported:
        raise ValueError(
            f"Invalid output format {fmt!r}: expected one of {supported!r}."
        )
    return fmt


def assert_extension(path: str, supported: list[str]) -> str:
    if not any(path.endswith(ext) for ext in supported):
        raise ValueError(
            f"Invalid database path {path!r}: expected one of extensions {supported!r}."
        )
    return path


def is_yearly_period(period: str) -> bool:
    assert_period(period)
    return "-" not in period


def is_monthly_period(period: str) -> bool:
    assert_period(period)
    return "-" in period
