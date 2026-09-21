"""Discovers Capital Bikeshare trip-data ZIP archives published on S3.

Handles ListObjectsV2 XML parsing (with pagination) and classification of
object keys into canonical periods ('YYYY' for yearly files, 'YYYY-MM' for
monthly files). Keys that don't match either known naming convention are
never silently dropped -- they're surfaced as `unclassified` so schema
drift in the bucket's naming is visible.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from capital_bikeshare_extractor.config import Config
from capital_bikeshare_extractor.http import SESSION

_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

_YEAR_RE = re.compile(r"^(?P<year>\d{4})-capitalbikeshare-tripdata\.zip$")
_MONTH_RE = re.compile(r"^(?P<yearmonth>\d{6})-capitalbikeshare-tripdata\.zip$")


@dataclass(frozen=True)
class S3Object:
    key: str
    size: int
    etag: str


@dataclass(frozen=True)
class PeriodInfo:
    period: str
    key: str
    size: int
    etag: str


def classify_key(key: str) -> str | None:
    """Return the canonical period string for an S3 key, or None if the key
    doesn't match either the yearly or monthly naming convention."""
    year_match = _YEAR_RE.match(key)
    if year_match:
        return year_match.group("year")

    month_match = _MONTH_RE.match(key)
    if month_match:
        yearmonth = month_match.group("yearmonth")
        return f"{yearmonth[:4]}-{yearmonth[4:]}"

    return None


def parse_list_bucket_result(xml_bytes: bytes) -> tuple[list[S3Object], bool, str | None]:
    """Parse one page of a ListObjectsV2 response.

    Returns (objects, is_truncated, next_continuation_token).
    """
    root = ET.fromstring(xml_bytes)
    is_truncated = root.findtext("s3:IsTruncated", namespaces=_NS) == "true"
    token = root.findtext("s3:NextContinuationToken", namespaces=_NS)

    objects: list[S3Object] = []
    for contents in root.findall("s3:Contents", _NS):
        key = contents.findtext("s3:Key", namespaces=_NS)
        size_text = contents.findtext("s3:Size", namespaces=_NS)
        etag = contents.findtext("s3:ETag", namespaces=_NS)
        if key is None or size_text is None:
            continue
        objects.append(S3Object(key=key, size=int(size_text), etag=etag or ""))

    return objects, is_truncated, token


def list_s3_objects(config: Config) -> list[S3Object]:
    """Fetch the full (paginated) object listing from the S3 bucket."""
    objects: list[S3Object] = []
    token: str | None = None

    while True:
        url = config.s3_list_url
        if token:
            url = f"{url}&continuation-token={token}"
        response = SESSION.get(url, timeout=30)
        response.raise_for_status()

        page_objects, is_truncated, next_token = parse_list_bucket_result(response.content)
        objects.extend(page_objects)

        if not is_truncated or not next_token:
            break
        token = next_token

    return objects


def discover_periods(config: Config) -> tuple[dict[str, PeriodInfo], list[str]]:
    """List and classify all S3 objects.

    Returns (periods, unclassified_keys) where `periods` maps canonical
    period string -> PeriodInfo, and `unclassified_keys` lists any object
    keys that didn't match a known naming convention.
    """
    periods: dict[str, PeriodInfo] = {}
    unclassified: list[str] = []

    for obj in list_s3_objects(config):
        period = classify_key(obj.key)
        if period is None:
            unclassified.append(obj.key)
            continue
        periods[period] = PeriodInfo(period=period, key=obj.key, size=obj.size, etag=obj.etag)

    return periods, unclassified
