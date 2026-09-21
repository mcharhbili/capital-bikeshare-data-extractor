import sqlite3

import pandas as pd
import pytest
import responses

from capital_bikeshare_extractor.store import ParquetStore, SqliteStore, make_store


def _write_csv(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


TRIPS_CSV_HEADER = (
    "ride_id,rideable_type,started_at,ended_at,start_station_name,start_station_id,"
    "end_station_name,end_station_id,start_lat,start_lng,end_lat,end_lng,member_casual\n"
)


def test_make_store_dispatches_on_backend(tmp_path):
    sqlite_store = make_store("sqlite", tmp_path / "db.sqlite", tmp_path / "parquet")
    assert isinstance(sqlite_store, SqliteStore)

    parquet_store = make_store("parquet", tmp_path / "db.sqlite", tmp_path / "parquet")
    assert isinstance(parquet_store, ParquetStore)

    with pytest.raises(ValueError):
        make_store("csv", tmp_path / "db.sqlite", tmp_path / "parquet")


def test_sqlite_store_load_period_writes_to_raw_trips(tmp_path, config):
    csv_path = _write_csv(
        tmp_path,
        "trips.csv",
        TRIPS_CSV_HEADER
        + "r1,classic_bike,2023-01-01 00:00:00,2023-01-01 00:10:00,A,1,B,2,38.9,-77.0,38.91,-77.01,member\n",
    )
    store = SqliteStore(tmp_path / "db.sqlite")
    store.init()

    rows_deleted, rows_inserted = store.load_period(
        config, [csv_path], period="2023-01", source_key="k", synced_at="t1"
    )
    assert rows_deleted == 0
    assert rows_inserted == 1

    conn = sqlite3.connect(tmp_path / "db.sqlite")
    try:
        count = conn.execute("SELECT COUNT(*) FROM raw_trips").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_parquet_store_load_period_partitions_by_year_month(tmp_path, config):
    csv_path = _write_csv(
        tmp_path,
        "trips.csv",
        TRIPS_CSV_HEADER
        + "r1,classic_bike,2023-05-01 00:00:00,2023-05-01 00:10:00,A,1,B,2,38.9,-77.0,38.91,-77.01,member\n"
        "r2,classic_bike,2023-05-02 00:00:00,2023-05-02 00:10:00,A,1,B,2,38.9,-77.0,38.91,-77.01,casual\n",
    )
    store = ParquetStore(tmp_path / "parquet")

    rows_deleted, rows_inserted = store.load_period(
        config, [csv_path], period="2023-05", source_key="k", synced_at="t1"
    )

    assert rows_deleted == 0
    assert rows_inserted == 2

    out_path = tmp_path / "parquet" / "raw_trips" / "year=2023" / "month=05" / "period=2023-05.parquet"
    assert out_path.exists()

    df = pd.read_parquet(out_path)
    assert len(df) == 2
    assert set(df["ride_id"]) == {"r1", "r2"}


def test_parquet_store_load_period_is_idempotent_on_resync(tmp_path, config):
    csv_path = _write_csv(
        tmp_path,
        "trips.csv",
        TRIPS_CSV_HEADER
        + "r1,classic_bike,2023-05-01 00:00:00,2023-05-01 00:10:00,A,1,B,2,38.9,-77.0,38.91,-77.01,member\n",
    )
    store = ParquetStore(tmp_path / "parquet")

    rows_deleted_1, rows_inserted_1 = store.load_period(
        config, [csv_path], period="2023-05", source_key="k", synced_at="t1"
    )
    rows_deleted_2, rows_inserted_2 = store.load_period(
        config, [csv_path], period="2023-05", source_key="k", synced_at="t2"
    )

    assert rows_deleted_1 == 0
    assert rows_inserted_1 == 1
    assert rows_deleted_2 == 1
    assert rows_inserted_2 == 1

    out_path = tmp_path / "parquet" / "raw_trips" / "year=2023" / "month=05" / "period=2023-05.parquet"
    df = pd.read_parquet(out_path)
    assert len(df) == 1  # not doubled
    assert df.iloc[0]["synced_at"] == "t2"


def test_parquet_store_yearly_period_uses_month_00(tmp_path, config):
    csv_path = _write_csv(
        tmp_path,
        "trips.csv",
        "Duration,Start date,End date,Start station,Start station number,End station,"
        "End station number,Bike#,Member Type\n"
        "600,2016-01-01 00:00:00,2016-01-01 00:10:00,A,1,B,2,W1,Member\n",
    )
    store = ParquetStore(tmp_path / "parquet")

    store.load_period(config, [csv_path], period="2016", source_key="k", synced_at="t1")

    out_path = tmp_path / "parquet" / "raw_trips" / "year=2016" / "month=00" / "period=2016.parquet"
    assert out_path.exists()


@responses.activate
def test_parquet_store_sync_stations_writes_single_file(tmp_path, config):
    responses.add(
        responses.GET,
        config.gbfs_station_information_url,
        json={
            "data": {
                "stations": [
                    {
                        "station_id": "S1",
                        "short_name": "100",
                        "name": "Station One",
                        "lat": 38.9,
                        "lon": -77.0,
                        "capacity": 15,
                    }
                ]
            }
        },
        status=200,
    )

    store = ParquetStore(tmp_path / "parquet")
    count = store.sync_stations(config, synced_at="t1")

    assert count == 1
    stations_path = tmp_path / "parquet" / "stations.parquet"
    assert stations_path.exists()

    df = pd.read_parquet(stations_path)
    assert len(df) == 1
    assert df.iloc[0]["station_id"] == "S1"


@responses.activate
def test_parquet_store_sync_stations_overwrites_on_refresh(tmp_path, config):
    def _respond(name):
        responses.add(
            responses.GET,
            config.gbfs_station_information_url,
            json={
                "data": {
                    "stations": [
                        {
                            "station_id": "S1",
                            "short_name": "100",
                            "name": name,
                            "lat": 38.9,
                            "lon": -77.0,
                            "capacity": 15,
                        }
                    ]
                }
            },
            status=200,
        )

    _respond("First Name")
    _respond("Renamed")

    store = ParquetStore(tmp_path / "parquet")
    store.sync_stations(config, synced_at="t1")
    store.sync_stations(config, synced_at="t2")

    df = pd.read_parquet(tmp_path / "parquet" / "stations.parquet")
    assert len(df) == 1
    assert df.iloc[0]["name"] == "Renamed"
