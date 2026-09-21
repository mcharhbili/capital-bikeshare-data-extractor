import responses

from capital_bikeshare_extractor.stations import (
    StationRecord,
    fetch_station_information,
    sync_stations,
    upsert_stations,
)


def test_upsert_stations_inserts_then_updates(memory_conn):
    station = StationRecord(
        station_id="S1", short_name="100", name="First Name", lat=38.9, lon=-77.0, capacity=10
    )
    upsert_stations(memory_conn, [station], synced_at="t1")

    row = memory_conn.execute(
        "SELECT name, capacity, updated_at FROM stations WHERE station_id = 'S1'"
    ).fetchone()
    assert row == ("First Name", 10, "t1")

    updated_station = StationRecord(
        station_id="S1", short_name="100", name="Renamed", lat=38.9, lon=-77.0, capacity=20
    )
    upsert_stations(memory_conn, [updated_station], synced_at="t2")

    count = memory_conn.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
    row = memory_conn.execute(
        "SELECT name, capacity, updated_at FROM stations WHERE station_id = 'S1'"
    ).fetchone()

    assert count == 1
    assert row == ("Renamed", 20, "t2")


@responses.activate
def test_fetch_station_information_parses_gbfs_payload(config):
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

    stations = fetch_station_information(config)

    assert len(stations) == 1
    assert stations[0].station_id == "S1"
    assert stations[0].short_name == "100"
    assert stations[0].capacity == 15


@responses.activate
def test_sync_stations_fetches_and_upserts(memory_conn, config):
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

    count = sync_stations(config, memory_conn, synced_at="t1")

    assert count == 1
    row = memory_conn.execute("SELECT name FROM stations WHERE station_id = 'S1'").fetchone()
    assert row == ("Station One",)
