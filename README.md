# Capital Bikeshare Data Extractor

A Python CLI tool that syncs [Capital Bikeshare](https://capitalbikeshare.com/system-data) trip data from S3 into a local SQLite database, with resumable/incremental tracking and live station data via [GBFS](https://gbfs.org/).

## Architecture at a glance

```
S3 bucket (capitalbikeshare-data)
   |  list + classify (yearly 2010-2017, monthly 2018+)
   v
manifest.json  <---- diff -----  fresh S3 listing
   |  (new / updated / Completed per period)
   v
download zip -> extract csv -> normalize columns -> raw_trips (SQLite)

GBFS station_information.json --- upsert ---------->  stations
```

## Installation

Requires Python 3.10+.

```bash
pip install -e .
```

For running the test suite:

```bash
pip install -e ".[dev]"
```

## Quickstart

```bash
cbse init-db                       # create the SQLite schema
cbse list-files                    # see what's available on S3
cbse plan --latest-only            # preview what a sync would do
cbse sync-period 2018-01           # sync one month
cbse sync-stations                 # pull live GBFS station data
```

Then query the trip data:

```bash
sqlite3 data/bikeshare.db "SELECT * FROM raw_trips LIMIT 5;"
```

## CLI reference

Global flags: `--db PATH` (default `data/bikeshare.db`), `--manifest PATH` (default `data/manifest.json`), `--output-format {print,dict,json}`.

| Subcommand | Arguments | Description |
|---|---|---|
| `list-files` | | List and classify all S3 trip-data files |
| `manifest show` | | Show current manifest contents |
| `manifest diff` | | Diff live S3 listing against the manifest (no save) |
| `periods` | | Show all periods discovered on S3 |
| `plan` | `--latest-only` | Preview what `sync-pending` would do |
| `init-db` | `--force` | Create the database schema |
| `download` | `period`, `--force` | Download a period's ZIP archive |
| `extract` | `period`, `--force` | Extract a downloaded ZIP archive |
| `process` | `period`, `--force` | Normalize + load an extracted period into SQLite |
| `sync-period` | `period`, `--force`, `--dry-run` | Download, extract, normalize, and load one period |
| `sync-range` | `start`, `end`, `--force`, `--dry-run` | Sync a contiguous range of periods |
| `sync-pending` | `--latest-only`, `--force`, `--dry-run` | Sync everything the manifest marks as pending |
| `sync-stations` | `--dry-run` | Refresh live GBFS station data |

`--force` bypasses the manifest's "already completed" short-circuit and re-syncs anyway. `--dry-run` reports what would happen without downloading or writing anything.

## Data model

**`raw_trips`** — one row per trip per source file. No uniqueness constraint on `ride_id`: a trip spanning a month boundary can legitimately appear in both its start month's and end month's file. Re-syncing a period deletes and reinserts only that period's rows (scoped by `period` + `source_key`), so re-runs are safe.

**`stations`** — a full-refresh upsert of the live GBFS `station_information` feed, keyed by `station_id`. Also stores `short_name`, the legacy numeric station code that historical trip files reference in `start_station_id`/`end_station_id` (GBFS's `station_id` is a UUID, not the code trip data uses).

## Manifest

`data/manifest.json` tracks, per period (`"2016"` or `"2018-01"`), the S3 object key, size, ETag, and sync `status`:

- `new` — seen on S3, never synced
- `updated` — synced before, but the file has changed on S3 since (reopens a `Completed` period)
- `Completed` — synced and unchanged since

The manifest is only marked `Completed` after a period's database transaction commits, so it never claims a sync succeeded if it didn't.

## Configuration

`capital_bikeshare_extractor/config.yaml` holds the S3/GBFS URLs, supported output formats, accepted database extensions, and — most importantly — `column_renames`: a flat map from every observed historical CSV header to its canonical column name. To support a newly-observed header variant, add one entry here; no code changes needed.

## Schema drift / error handling

If a CSV contains a column not present in `column_renames`, the load aborts immediately with an error listing the offending column name(s) and source file — it never silently drops unrecognized data. Add the missing mapping to `config.yaml` and re-run.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests use in-memory SQLite, `tmp_path`, and mocked HTTP (`responses`) — no live network or real disk state required.

## Known limitations

- SQLite-only backend, single-threaded downloads.
- GBFS URL is hardcoded to Lyft's `dca-cabi` feed rather than resolved via GBFS auto-discovery.

## License

MIT
