# Capital Bikeshare Data Extractor

A Python CLI tool that syncs [Capital Bikeshare](https://capitalbikeshare.com/system-data) trip data from S3 into a local **Parquet** dataset, with resumable/incremental tracking and live station data via [GBFS](https://gbfs.org/).

## Architecture at a glance

```
S3 bucket (capitalbikeshare-data)
   |  list + classify (yearly 2010-2017, monthly 2018+)
   v
manifest.json  <---- diff -----  fresh S3 listing
   |  (new / updated / Completed per period)
   v
download zip -> extract csv -> normalize columns -> raw_trips (Parquet,
                                                      partitioned by
                                                      year=YYYY/month=MM)

GBFS station_information.json --- upsert ---------->  stations.parquet
```

## Installation

Requires Python 3.10+.

```bash
pip install -e .
```

## Quickstart

```bash
cbse init-db                       # create the Parquet output directory
cbse list-files                    # see what's available on S3
cbse plan --latest-only            # preview what a sync would do
cbse sync-period 2018-01           # sync one month
cbse sync-stations                 # pull live GBFS station data
```

Then query the trip data:

```bash
python -c "import pandas as pd; print(pd.read_parquet('data/parquet/raw_trips').head())"
```

## CLI reference

Global flags: `--data-dir PATH` (default `data/parquet`), `--manifest PATH` (default `data/manifest.json`), `--output-format {print,dict,json}` (default `print`), `-v/--verbose` (debug logging).

| Subcommand | Arguments | Description |
|---|---|---|
| `list-files` | | List and classify all S3 trip-data files |
| `manifest show` | | Show current manifest contents |
| `manifest diff` | | Diff live S3 listing against the manifest (no save) |
| `periods` | | Show all periods discovered on S3 |
| `plan` | `--latest-only` | Preview what `sync-pending` would do |
| `init-db` | | Create the Parquet output directory |
| `download` | `period`, `--force` | Download a period's ZIP archive |
| `extract` | `period`, `--force` | Extract a downloaded ZIP archive |
| `process` | `period`, `--force` | Normalize + load an extracted period into Parquet |
| `sync-period` | `period`, `--force`, `--dry-run` | Download, extract, normalize, and load one period |
| `sync-range` | `start`, `end`, `--force`, `--dry-run` | Sync a contiguous range of periods (both endpoints must be the same kind — both yearly or both monthly) |
| `sync-pending` | `--latest-only`, `--force`, `--dry-run` | Sync everything the manifest marks as pending (new + updated) |
| `sync-stations` | `--dry-run` | Refresh live GBFS station data |

`--force` bypasses the manifest's "already completed" short-circuit and re-syncs anyway. `--dry-run` reports what would happen without downloading or writing anything. `period` is either `YYYY` (pre-2018 yearly files) or `YYYY-MM` (2018+ monthly files).

## Storage

Trip data is stored as one Parquet file per period, partitioned into `year=YYYY/month=MM/` directories under `data_dir/raw_trips/` — good for analytics (pandas/DuckDB/Spark) over large trip histories:

```
data/parquet/
  raw_trips/
    year=2018/
      month=01/period=2018-01.parquet
      month=02/period=2018-02.parquet
      ...
    year=2016/
      month=00/period=2016.parquet   # pre-2018 yearly files have no month
  stations.parquet
```

Each period is written as a single file, replaced wholesale on re-sync (write-to-temp-file then atomic rename), so a crash mid-write can't leave a half-written period. `stations.parquet` is a full-refresh single file, rewritten the same way on every `sync-stations`.

## Data model

**`raw_trips`** — one row per trip per source file: `ride_id`, `rideable_type`, `started_at`, `ended_at`, `start_station_id`, `start_station_name`, `end_station_id`, `end_station_name`, `start_lat`, `start_lng`, `end_lat`, `end_lng`, `member_casual`, `duration`, plus provenance columns `period`, `source_key`, `synced_at`. No uniqueness constraint on `ride_id`: a trip spanning a month boundary can legitimately appear in both its start month's and end month's file.

**`stations`** — a full-refresh upsert of the live GBFS `station_information` feed, keyed by `station_id`: `short_name`, `name`, `lat`, `lon`, `capacity`, `updated_at`. `short_name` is the legacy numeric station code that historical trip files reference in `start_station_id`/`end_station_id` (GBFS's `station_id` is a UUID, not the code trip data uses).

## Manifest

`data/manifest.json` tracks, per period (`"2016"` or `"2018-01"`), the S3 object key, size, ETag, and sync `status`:

- `new` — seen on S3, never synced
- `updated` — synced before, but the file has changed on S3 since (reopens a `Completed` period)
- `Completed` — synced and unchanged since

`sync-pending`/`plan` treat `new` and `updated` periods as pending. The manifest is only marked `Completed` after a period's write commits, so it never claims a sync succeeded if it didn't. Periods that disappear from the S3 listing are left untouched in the manifest — local data for them is never deleted based on a diff.

## Configuration

`capital_bikeshare_extractor/config.yaml` holds the S3/GBFS URLs, supported output formats, and — most importantly — `column_renames`: a flat map from every observed historical CSV header to its canonical column name. To support a newly-observed header variant, add one entry here; no code changes needed.

## Schema drift / error handling

If a CSV contains a column not present in `column_renames`, the load aborts immediately with an error listing the offending column name(s) and source file — it never silently drops unrecognized data. Add the missing mapping to `config.yaml` and re-run. Likewise, S3 object keys that don't match either the yearly or monthly naming convention are surfaced as `unclassified_keys` (via `list-files`) rather than silently skipped.

## Known limitations

- Single-threaded downloads.
- GBFS URL is hardcoded to Lyft's `dca-cabi` feed rather than resolved via GBFS auto-discovery.

## License

MIT
