import sqlite3
import shutil
import zipfile
import json
import logging
import re
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from tqdm import tqdm
import xml.etree.ElementTree as ET
from pathlib import Path
import datetime as dt
from dateutil.relativedelta import relativedelta
from capital_bikeshare_extractor.config import (S3_LIST_URL,
                        S3_OBJECT_URL,
                        GBFS_STATION_INFORMATION_URL,
                        SUPPORTED_OUTPUT_FORMATS,
                        COLUMN_RENAMES,
                        COLUMN_RENAMES_TRIP_RAW,
                        BACKEND_EXTENSIONS)

from capital_bikeshare_extractor.validation import _assert_output_format, _assert_period, _assert_json_manifest_path
from pathlib import Path
import glob


logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG)
logger.propagate = False

def _build_http_session():
    """Build a requests Session that retries transient network/server errors."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

HTTP_SESSION = _build_http_session()

def format_size(size):
    """Convert a byte count to a human-readable size."""
    size = int(size)
    units = ("bytes", "KB", "MB", "GB", "TB")

    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}" if unit != "bytes" else f"{size} {unit}"
        size /= 1024

def get_data_period(key):
    """Classify a file key as monthly, yearly, or unknown data."""
    if re.match(r"^\d{6}-", key):
        return "monthly", key[:6]
    if re.match(r"^\d{4}-", key):
        return "yearly", key[:4]
    return "unknown", None

def create_sqlite_db_if_not_exists(db_path="db.sqlite"):
    """Create an SQLite database file if it does not already exist.

    Parent directories are created when needed. Existing database files are
    opened and left unchanged.
    """
    database_path = Path(db_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_existed = database_path.exists()

    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()

    # Execute CREATE TABLE statement
    # ride_id has no uniqueness constraint: Capital Bikeshare's monthly
    # exports aren't perfectly partitioned by started_at, so a trip spanning
    # a month boundary can appear in two adjacent files under the same
    # ride_id. A PRIMARY KEY/UNIQUE constraint here would make syncing both
    # periods raise a UNIQUE constraint error; duplicates are tolerated
    # instead and can be deduped downstream if needed.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS raw_trips (
        ride_id TEXT,
        rideable_type TEXT,
        started_at TEXT,
        ended_at TEXT,
        start_station_name TEXT,
        start_station_id TEXT,
        end_station_name TEXT,
        end_station_id TEXT,
        start_lat REAL,
        start_lng REAL,
        end_lat REAL,
        end_lng REAL,
        member_casual TEXT,
        duration INTEGER,
        year integer,
        month integer
    );
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_raw_trips_year_month
    ON raw_trips (year, month);
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_raw_trips_ride_id
    ON raw_trips (ride_id);
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stations (
        station_id TEXT PRIMARY KEY,
        name TEXT,
        short_name TEXT,
        lat REAL,
        lon REAL,
        region_id TEXT,
        capacity INTEGER,
        station_type TEXT,
        last_updated TEXT
    );
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_stations_short_name_int
    ON stations (CAST(short_name AS INTEGER));
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_raw_trips_start_station_id_int
    ON raw_trips (CAST(start_station_id AS INTEGER));
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_raw_trips_end_station_id_int
    ON raw_trips (CAST(end_station_id AS INTEGER));
    """)

    # Older Capital Bikeshare exports include trip duration as a dedicated
    # column. Migrate databases created by earlier versions of the extractor
    # so those files can be appended without requiring users to recreate the
    # database.
    raw_trip_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(raw_trips)")
    }
    if "duration" not in raw_trip_columns:
        cursor.execute("ALTER TABLE raw_trips ADD COLUMN duration INTEGER")
        logger.info("Added missing duration column to raw_trips in %s", database_path)

    # Databases created before ride_id's PRIMARY KEY constraint was removed
    # still enforce it. SQLite can't drop a column constraint via ALTER
    # TABLE, so rebuild the table without it, preserving all existing rows.
    ride_id_is_pk = any(
        row[1] == "ride_id" and row[5] > 0
        for row in cursor.execute("PRAGMA table_info(raw_trips)")
    )
    if ride_id_is_pk:
        logger.info("Migrating raw_trips to drop the ride_id PRIMARY KEY constraint in %s", database_path)
        cursor.execute("ALTER TABLE raw_trips RENAME TO raw_trips_old")
        cursor.execute("""
        CREATE TABLE raw_trips (
            ride_id TEXT,
            rideable_type TEXT,
            started_at TEXT,
            ended_at TEXT,
            start_station_name TEXT,
            start_station_id TEXT,
            end_station_name TEXT,
            end_station_id TEXT,
            start_lat REAL,
            start_lng REAL,
            end_lat REAL,
            end_lng REAL,
            member_casual TEXT,
            duration INTEGER,
            year integer,
            month integer
        );
        """)
        columns = ", ".join(row[1] for row in cursor.execute("PRAGMA table_info(raw_trips_old)"))
        cursor.execute(f"INSERT INTO raw_trips ({columns}) SELECT {columns} FROM raw_trips_old")
        cursor.execute("DROP TABLE raw_trips_old")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_raw_trips_year_month ON raw_trips (year, month)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_raw_trips_ride_id ON raw_trips (ride_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_raw_trips_start_station_id_int ON raw_trips (CAST(start_station_id AS INTEGER))")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_raw_trips_end_station_id_int ON raw_trips (CAST(end_station_id AS INTEGER))")

    # Enriches each trip with the current GBFS snapshot for its start/end
    # station. raw_trips.start_station_id/end_station_id are the legacy
    # numeric station codes, which match stations.short_name rather than
    # stations.station_id (a GBFS UUID) - hence the CAST-based join instead
    # of a direct id match. A LEFT JOIN keeps every trip row even when a
    # station has since been removed from the live system or the id is
    # missing/unparseable. Created after any raw_trips migration above so
    # the view always points at the current table definition.
    #
    # raw_trips.ride_id carries no uniqueness constraint (a trip spanning a
    # month boundary can appear in two adjacent monthly files under the same
    # ride_id - see create_sqlite_db_if_not_exists), so a non-null ride_id
    # can occur more than once in raw_trips. To dedupe without forcing a
    # window-function pass over the entire (potentially tens-of-millions-of-
    # rows) table on every query, this only computes ROW_NUMBER() over the
    # small set of ride_ids that actually repeat (found via the ride_id
    # index) and UNIONs it with every other row - including all null-ride_id
    # rows - passed through untouched.
    cursor.execute("DROP VIEW IF EXISTS trips_enriched")
    cursor.execute("""
    CREATE VIEW trips_enriched AS
    WITH duplicated_ride_ids AS (
        SELECT ride_id
        FROM raw_trips
        WHERE ride_id IS NOT NULL
        GROUP BY ride_id
        HAVING COUNT(*) > 1
    ),
    deduped_repeats AS (
        SELECT
            t.ride_id, t.rideable_type, t.started_at, t.ended_at,
            t.start_station_name, t.start_station_id,
            t.end_station_name, t.end_station_id,
            t.start_lat, t.start_lng, t.end_lat, t.end_lng,
            t.member_casual, t.duration, t.year, t.month
        FROM (
            SELECT
                t.*,
                ROW_NUMBER() OVER (
                    PARTITION BY t.ride_id
                    ORDER BY t.year DESC, t.month DESC
                ) AS rn
            FROM raw_trips AS t
            JOIN duplicated_ride_ids AS d ON d.ride_id = t.ride_id
        ) AS t
        WHERE t.rn = 1
    ),
    deduped_trips AS (
        SELECT
            t.ride_id, t.rideable_type, t.started_at, t.ended_at,
            t.start_station_name, t.start_station_id,
            t.end_station_name, t.end_station_id,
            t.start_lat, t.start_lng, t.end_lat, t.end_lng,
            t.member_casual, t.duration, t.year, t.month
        FROM raw_trips AS t
        WHERE t.ride_id IS NULL
           OR t.ride_id NOT IN (SELECT ride_id FROM duplicated_ride_ids)
        UNION ALL
        SELECT * FROM deduped_repeats
    )
    SELECT
        t.ride_id,
        t.rideable_type,
        t.started_at,
        t.ended_at,
        t.member_casual,
        t.duration,
        t.year,
        t.month,
        t.start_station_id,
        t.start_station_name,
        COALESCE(start_station.lat, t.start_lat) AS start_lat,
        COALESCE(start_station.lon, t.start_lng) AS start_lng,
        start_station.name AS start_station_current_name,
        start_station.capacity AS start_station_capacity,
        start_station.region_id AS start_station_region_id,
        t.end_station_id,
        t.end_station_name,
        COALESCE(end_station.lat, t.end_lat) AS end_lat,
        COALESCE(end_station.lon, t.end_lng) AS end_lng,
        end_station.name AS end_station_current_name,
        end_station.capacity AS end_station_capacity,
        end_station.region_id AS end_station_region_id
    FROM deduped_trips AS t
    LEFT JOIN stations AS start_station
        ON CAST(start_station.short_name AS INTEGER) = CAST(t.start_station_id AS INTEGER)
    LEFT JOIN stations AS end_station
        ON CAST(end_station.short_name AS INTEGER) = CAST(t.end_station_id AS INTEGER)
    """)

    conn.commit()
    logger.info(
        "%s SQLite database: %s",
        "Opened existing" if database_existed else "Created",
        database_path,
    )

    return conn

def get_manifest(manifest_path):
    """Load a JSON manifest, creating an empty one when it is missing."""
    manifest_path = _assert_json_manifest_path(manifest_path)

    if not manifest_path.exists():
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("[]", encoding="utf-8")
        logger.info("Created empty JSON manifest: %s", manifest_path)
        return pd.DataFrame()

    with manifest_path.open("r", encoding="utf-8") as file:
        try:
            records = json.load(file)
        except json.JSONDecodeError:
            logger.warning("Manifest is empty or invalid JSON: %s", manifest_path)
            return pd.DataFrame()

    if not isinstance(records, list):
        raise ValueError("JSON manifest must contain a list of records.")

    logger.info("Loaded %d manifest records from %s", len(records), manifest_path)
    return pd.DataFrame(records)

def save_manifest(manifest_df, manifest_path):
    """Save a manifest DataFrame as a JSON list of records."""
    manifest_path = _assert_json_manifest_path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    with manifest_path.open("w", encoding="utf-8") as file:
        json.dump(manifest_df.to_dict(orient="records"), file, indent=2, default=str)
    logger.info("Saved %d manifest records to %s", len(manifest_df), manifest_path)

def get_s3_response():
    logger.info("Requesting Capital Bikeshare S3 file listing")
    response = HTTP_SESSION.get(S3_LIST_URL, timeout=30)
    response.raise_for_status()
    logger.info("Received S3 file listing")
    return response

def get_s3_list_item(item):
    key = item.find("{*}Key").text
    last_modified = item.find("{*}LastModified").text
    e_tag = item.find("{*}ETag").text
    size = item.find("{*}Size").text
    storage_class = item.find("{*}StorageClass").text
    if key.split('.')[-1]=="zip":
        return {
                "key": key,
                "last_modified": last_modified,
                "e_tag": e_tag,
                "size": size,
                "formated_size": format_size(size),
                "storage_class": storage_class,
                "granularity": get_data_period(key)[0],
                "period": get_data_period(key)[1],
                "url": S3_OBJECT_URL.format(key=key),
                "year":int(key[:4]),
                "month":int(key[4:6]) if len(key.split('-')[0])==6 else None,
        }
    return {}
    
def fetch_s3_available_files():
    response = get_s3_response()
    root = ET.fromstring(response.text)

    files = []

    for item in root.findall("{*}Contents"):
        file = get_s3_list_item(item)
        if file != {}:
            files.append(file)

    logger.info("Found %d downloadable ZIP files", len(files))
    return files

def fetch_station_information():
    """Fetch the current station location snapshot from the GBFS feed."""
    logger.info("Requesting GBFS station information")
    response = HTTP_SESSION.get(GBFS_STATION_INFORMATION_URL, timeout=30)
    response.raise_for_status()
    payload = response.json()

    last_updated = payload.get("last_updated")
    stations = payload["data"]["stations"]
    logger.info("Received %d stations from GBFS feed", len(stations))

    records = []
    for station in stations:
        records.append({
            "station_id": station.get("station_id"),
            "name": station.get("name"),
            "short_name": station.get("short_name"),
            "lat": station.get("lat"),
            "lon": station.get("lon"),
            "region_id": station.get("region_id"),
            "capacity": station.get("capacity"),
            "station_type": station.get("station_type"),
            "last_updated": last_updated,
        })

    return records

def sync_stations(db_path):
    """Fetch the current GBFS station snapshot and upsert it into the database."""
    stations = fetch_station_information()

    conn = create_sqlite_db_if_not_exists(db_path)
    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO stations (
                    station_id, name, short_name, lat, lon,
                    region_id, capacity, station_type, last_updated
                )
                VALUES (:station_id, :name, :short_name, :lat, :lon,
                        :region_id, :capacity, :station_type, :last_updated)
                ON CONFLICT(station_id) DO UPDATE SET
                    name=excluded.name,
                    short_name=excluded.short_name,
                    lat=excluded.lat,
                    lon=excluded.lon,
                    region_id=excluded.region_id,
                    capacity=excluded.capacity,
                    station_type=excluded.station_type,
                    last_updated=excluded.last_updated
                """,
                stations,
            )
    finally:
        conn.close()

    logger.info("Synchronized %d stations into %s", len(stations), db_path)
    return len(stations)

def display_s3_available_files(records, output_format):
    """Display records as aligned text, a Python list, or formatted JSON."""
    _assert_output_format(output_format)

    if output_format == "json":
        print(json.dumps(records, indent=2))
        return
    if output_format == "dict":
        print(records)
        return

    def getSize(k):
        return max((len(str(record[k])) for record in records), default=0)


    for record in records:
        status = f" | {record['status']}" if "status" in record else ""
        print(
            f"{str(record['key']):<{getSize('key')}} | "
            f"{str(record['size']):<{getSize('size')}} | "
            f"{str(record['formated_size']):>{getSize('formated_size')}} | "
            f"{str(record['granularity']):<{getSize('granularity')}} | "
            f"{str(record['period']):<{getSize('period')}} | "
            f"{str(record['url']):<{getSize('url')}} | "
            f"{str(record['last_modified']):<{getSize('last_modified')}} | "
            f"{str(record['year']):<{getSize('year')}} | "
            f"{str(record['month']):<{getSize('month')}} | "
            f"{status}"
        )

def _refresh_manifest(manifest_path, create):
    """Build or refresh the manifest from the live S3 listing and save it.

    Returns the resulting manifest DataFrame. Raises FileNotFoundError if the
    manifest is missing and ``create`` is False.
    """
    manifest_path = _assert_json_manifest_path(manifest_path)

    if not manifest_path.exists():
        if not create:
            raise FileNotFoundError(
                f"Manifest file does not exist: {manifest_path}"
            )

        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        files = fetch_s3_available_files()
        manifest_df = pd.DataFrame(files)
        manifest_df["status"] = "new"
        logger.info("Initialized manifest with %d new records", len(manifest_df))
    else:
        files = fetch_s3_available_files()
        manifest_df = get_manifest(manifest_path)

        if "status" not in manifest_df.columns:
            manifest_df["status"] = ""

        existing_records = {
            record["key"]: record
            for record in manifest_df.to_dict(orient="records")
            if record.get("key")
        }
        current_df = pd.DataFrame(files)
        compared_columns = [
            column for column in current_df.columns if column != "status"
        ]

        for record in current_df.to_dict(orient="records"):
            key = record["key"]
            previous = existing_records.get(key)

            if previous is None:
                record["status"] = "new"
            else:
                changed = any(
                    str(previous.get(column, "")) != str(record.get(column, ""))
                    for column in compared_columns
                )
                record["status"] = (
                    "updated" if changed else previous.get("status", "")
                )

            existing_records[key] = record

        manifest_df = pd.DataFrame(existing_records.values())

    save_manifest(manifest_df, manifest_path)

    status_counts = manifest_df.get("status", pd.Series(dtype=str)).value_counts()
    logger.info(
        "Manifest inspection complete: %d new, %d updated, %d total",
        int(status_counts.get("new", 0)),
        int(status_counts.get("updated", 0)),
        len(manifest_df),
    )

    return manifest_df

def inspect_changes_manifest(file_path, create, output_format="print"):
    _assert_output_format(output_format)

    if not file_path:
        raise ValueError("A manifest file path is required.")

    manifest_path = _assert_json_manifest_path(file_path)
    logger.info("Inspecting manifest changes: %s", manifest_path)

    manifest_df = _refresh_manifest(manifest_path, create)
    display_s3_available_files(manifest_df.to_dict(orient="records"), output_format)

    return manifest_path

def get_yearly_periods(manifest_path):
    df = pd.DataFrame(get_manifest(manifest_path))
    return df[df.granularity=="yearly"].period.unique()

def get_monthly_periods(manifest_path):
    df = pd.DataFrame(get_manifest(manifest_path))
    return df[df.granularity=="monthly"].period.unique()

def get_list_update_period(manifest_path, period, force):
    _assert_period(period)

    files_list = pd.DataFrame(get_manifest(manifest_path))

    if len(period) == 4:
        year = int(period)
        if not year in files_list.year.unique():
            raise Exception(f'{year} not available online!')
        
        if force:
            selected = files_list[files_list.year == year]
        else:
            selected = files_list[(files_list.year == year) & (files_list.status!="Completed")]
        logger.info("Selected %d files for period %s (force=%s)", len(selected), period, force)
        return selected

    elif len(period) == 7:
        year = int(period.split('-')[0])
        month = int(period.split('-')[1])
        if not year in files_list.year.unique():
            raise Exception(f'{year} not available online!')
        elif not month in files_list[files_list.year==year].month.unique():
            raise Exception(f'{year}-{month} not available online!')

        if force:
            selected = files_list[(files_list.year == year) & (files_list.month == month)]
        else:
            selected = files_list[(files_list.year == year) & (files_list.month == month) & (files_list.status!="Completed")]
        logger.info("Selected %d files for period %s (force=%s)", len(selected), period, force)
        return selected


def get_full_list_update(manifest_path, force):
    """Return every manifest record that should be processed."""
    files_list = pd.DataFrame(get_manifest(manifest_path))

    if force or "status" not in files_list.columns:
        selected = files_list
    else:
        selected = files_list[files_list.status != "Completed"]

    logger.info("Selected %d files for full sync (force=%s)", len(selected), force)
    return selected

def get_list_update_range(manifest_path, start, end, force):
    _assert_period(start)
    _assert_period(end)
    requested_start = start
    requested_end = end

    files_list = pd.DataFrame(get_manifest(manifest_path))

    if len(start)==4:
        start =  dt.date(int(start),1,1)
    else:
        start = dt.date(int(start.split('-')[0]),int(start.split('-')[1]),1)

    if len(end)==4:
        end =  dt.date(int(end),12,1)
    else:
        end = dt.date(int(end.split('-')[0]),int(end.split('-')[1]),1)

    if start >= end:
        raise ValueError(
            f"Start period must be before end period: {start} >= {end}"
        )

    yearly_periods = get_yearly_periods(manifest_path)
    monthly_periods = get_monthly_periods(manifest_path)

    dates_to_update = []
    start_p = start
    while start_p <= end:
        
        if str(start_p.year) in yearly_periods:
            dates_to_update.append(str(start_p.year))
        elif str(start_p.year*100+start_p.month) in monthly_periods:
            dates_to_update.append(str(start_p.year*100+start_p.month))
        start_p += relativedelta(months=1)

    dates_to_update = list(set(dates_to_update))

    dfs = []
    for i in dates_to_update:
        if len(i)>4:
            dfs.append(get_list_update_period(manifest_path, i[:4]+"-"+i[4:], force))
        else:
            dfs.append(get_list_update_period(manifest_path, i, force))

    selected = pd.concat(dfs)
    logger.info(
        "Selected %d files for range %s to %s (force=%s)",
        len(selected),
        requested_start,
        requested_end,
        force,
    )
    return selected

def download(key, path=".temp"):
    """Download an S3 ZIP object into ``path`` and return its local path."""
    url = f"https://s3.amazonaws.com/capitalbikeshare-data/{key}"
    destination_dir = Path(path)
    destination_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading %s to %s", key, destination_dir)

    response = HTTP_SESSION.get(url, timeout=30, stream=True)
    response.raise_for_status()

    total_size = int(response.headers.get("Content-Length", 0))
    archive_path = destination_dir / Path(key).name
    bytes_written = 0

    with archive_path.open("wb") as file, tqdm(
        total=total_size or None,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        desc=key,
        leave=False,
    ) as progress:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            file.write(chunk)
            bytes_written += len(chunk)
            progress.update(len(chunk))

    logger.info("Downloaded %s (%d bytes)", archive_path, bytes_written)
    return archive_path

def extract(key, path=".temp"):
    """Extract the downloaded ZIP identified by ``key`` into ``path``.

    The ZIP is expected to be located at ``path/<filename>``. The extracted
    files are written directly into ``path``.
    """
    destination_dir = Path(path)
    archive_path = destination_dir / Path(key).name

    if not archive_path.is_file():
        logger.error("ZIP archive does not exist: %s", archive_path)
        raise FileNotFoundError(f"ZIP file does not exist: {archive_path}")

    with zipfile.ZipFile(archive_path) as archive:
        member_count = len(archive.infolist())
        archive.extractall(destination_dir)

    logger.info("Extracted %d files from %s into %s", member_count, archive_path, destination_dir)
    return destination_dir

def get_all_csv_files(path=".temp"):
    """Return all CSV files directly inside ``path``.

    The returned paths are sorted by filename. A missing directory is treated
    as empty; a path that exists but is not a directory raises an error.
    """
    csv_dir = Path(path)

    if not csv_dir.exists():
        logger.info("CSV directory does not exist: %s", csv_dir)
        return []
    if not csv_dir.is_dir():
        raise NotADirectoryError(f"CSV path is not a directory: {csv_dir}")

    csv_files = sorted(
        file_path
        for file_path in csv_dir.iterdir()
        if file_path.is_file() and file_path.suffix.lower() == ".csv"
    )
    logger.info("Found %d CSV files in %s", len(csv_files), csv_dir)
    return csv_files

def process_sqlite(db_path, key, granularity, period, path=".temp"):
    logger.info("Starting SQLite processing for %s (%s)", period, key)
    conn = create_sqlite_db_if_not_exists(db_path)
    download(key, path)
    extract(key, path)
    dfs = []
    for i in get_all_csv_files(path):
        dfs.append(pd.read_csv(i))

    df = pd.concat(dfs)
    k, ks = 0, []
    for i in df.columns:
        if i not in COLUMN_RENAMES_TRIP_RAW.keys():
            k += 1
            ks.append(i)
    
    if k>0:
        logger.error("Unmapped input columns: %s", ", ".join(ks))
        raise Exception(f"Columns {' - '.join(ks)} are not mapped correctly, update the config.yaml file !")
    df = df.rename(columns=COLUMN_RENAMES_TRIP_RAW)
    year = int(period[:4])
    df["year"] = year

    if granularity == "yearly":
        dates = pd.to_datetime(df["started_at"], errors="coerce")
        if dates.isna().any():
            conn.close()
            raise ValueError("Some started_at values could not be parsed as dates.")
        df["month"] = dates.dt.month
        query = "DELETE FROM raw_trips WHERE year = ?"
        query_parameters = (year,)
    elif granularity == "monthly":
        month = int(period[4:6])
        df["month"] = month
        query = "DELETE FROM raw_trips WHERE year = ? AND month = ?"
        query_parameters = (year, month)
    else:
        conn.close()
        raise ValueError(f"Unsupported granularity: {granularity!r}")

    try:
        with conn:
            conn.execute(query, query_parameters)
            df.to_sql(
                name="raw_trips",
                con=conn,
                if_exists="append",
                index=False,
                chunksize=10000,
            )
    finally:
        conn.close()

    logger.info("Wrote %d rows to raw_trips in %s", len(df), db_path)
    return True

def process(manifest_path, db_path, key, granularity, period, path=".temp"):
    logger.info(
        "Processing period=%s granularity=%s",
        period,
        granularity,
    )

    destination_dir = Path(path)
    if destination_dir.exists() and not destination_dir.is_dir():
        raise NotADirectoryError(f"Update path is not a directory: {destination_dir}")

    if destination_dir.exists():
        for child in destination_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        destination_dir.mkdir(parents=True)

    process_sqlite(db_path, key, granularity, period, path)

    manifest = get_manifest(manifest_path)
    manifest.loc[(manifest.granularity==granularity) & (manifest.period==period),"status"] = "Completed"
    save_manifest(manifest, manifest_path)
    logger.info("Marked %s (%s) as Completed", period, granularity)

def _ensure_manifest_exists(manifest_path):
    """Populate the manifest from the live S3 listing if it doesn't exist yet."""
    manifest_path = _assert_json_manifest_path(manifest_path)
    if not manifest_path.exists():
        logger.info("Manifest not found, creating it from the S3 listing: %s", manifest_path)
        _refresh_manifest(manifest_path, create=True)

def sync_period(db_path, manifest_path, period, path=".temp", force=False, dry_run=False):
    _ensure_manifest_exists(manifest_path)

    df_list_update = get_list_update_period(manifest_path, period, force)
    if dry_run:
        logger.info("Dry run: %d files would be synchronized for period %s", len(df_list_update), period)
        return df_list_update

    logger.info("Starting period sync with %d files", len(df_list_update))

    for _,row in df_list_update.iterrows():
        process(manifest_path, db_path, row['key'],row['granularity'],row['period'],path=path)
    logger.info("Period sync completed for %s", period)

def sync_range(db_path, manifest_path, start, end, path=".temp", force=False, dry_run=False):
    _ensure_manifest_exists(manifest_path)

    df_list_update = get_list_update_range(manifest_path, start, end, force)
    if dry_run:
        logger.info("Dry run: %d files would be synchronized for range %s to %s", len(df_list_update), start, end)
        return df_list_update

    logger.info("Starting range sync with %d files", len(df_list_update))

    for _,row in df_list_update.iterrows():
        process(manifest_path, db_path, row['key'],row['granularity'],row['period'],path=path)
    logger.info("Range sync completed for %s to %s", start, end)



def sync(db_path, manifest_path, path=".temp", force=False, dry_run=False):
    """Synchronize all pending records in the manifest."""
    _ensure_manifest_exists(manifest_path)

    df_list_update = get_full_list_update(manifest_path, force)
    if dry_run:
        logger.info("Dry run: %d files would be synchronized", len(df_list_update))
        return df_list_update

    logger.info("Starting full sync with %d files", len(df_list_update))

    for _, row in df_list_update.iterrows():
        process(
            manifest_path,
            db_path,
            row["key"],
            row["granularity"],
            row["period"],
            path=path,
        )

    logger.info("Full sync completed")

def get_latest_period(manifest_path, force=False):
    """Return the single most recent (year, month) period that should be synced."""
    _ensure_manifest_exists(manifest_path)
    files_list = get_full_list_update(manifest_path, force)

    if len(files_list) == 0:
        raise ValueError("No pending periods found in the manifest.")

    sortable = files_list.copy()
    sortable["month"] = sortable["month"].fillna(0).astype(int)
    sortable = sortable.sort_values(["year", "month"], ascending=False)

    latest = sortable.iloc[0]
    if latest["granularity"] == "monthly":
        return f"{int(latest['year'])}-{int(latest['month']):02d}"
    return str(int(latest["year"]))

