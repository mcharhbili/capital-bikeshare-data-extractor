from capital_bikeshare_extractor.config import SUPPORTED_OUTPUT_FORMATS, BACKEND_EXTENSIONS
import re
from pathlib import Path


def _assert_output_format(output_format):
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError(
            f"Unsupported output format: {output_format!r}. "
            f"Format must be one of {sorted(SUPPORTED_OUTPUT_FORMATS)}."
        )

def _assert_extension(manifest_path):
    if manifest_path.exists() and manifest_path.suffix.lower() not in BACKEND_EXTENSIONS["sqlite"]:
        expected = ", ".join(sorted(BACKEND_EXTENSIONS["sqlite"]))
        raise ValueError(
            f"Destination file has an invalid type: {manifest_path}. "
            f"Expected extension(s): {expected}."
        )


def _assert_period(period):
    if not isinstance(period, str) or not re.fullmatch(
        r"(?:\d{4}|\d{4}-(?:0[1-9]|1[0-2]))", period
    ):
        raise ValueError(
            f"Invalid period: {period!r}. "
            "Period must use YYYY or YYYY-MM format."
        )

def _assert_json_manifest_path(manifest_path):
    manifest_path = Path(manifest_path)
    if manifest_path.suffix.lower() != ".json":
        raise ValueError(
            f"Manifest path must be a JSON file: {manifest_path}"
        )
    return manifest_path
