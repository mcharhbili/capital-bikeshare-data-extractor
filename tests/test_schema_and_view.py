import sqlite3

from capital_bikeshare_extractor.schema import init_db, refresh_trips_enriched


def _insert_trip(conn, **kwargs):
    defaults = dict(
        ride_id=None,
        rideable_type="classic",
        started_at="2020-01-01T00:00:00",
        ended_at="2020-01-01T00:10:00",
        start_station_id=None,
        end_station_id=None,
        start_lat=None,
        start_lng=None,
        end_lat=None,
        end_lng=None,
        member_casual="member",
        duration=None,
        period="2020-01",
        source_key="k",
        synced_at="2020-01-01T00:00:00",
    )
    defaults.update(kwargs)
    columns = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    conn.execute(
        f"INSERT INTO raw_trips ({columns}) VALUES ({placeholders})",
        list(defaults.values()),
    )


def test_init_db_is_idempotent():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    init_db(conn)  # must not raise

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    assert {"raw_trips", "stations", "trips_enriched_view", "trips_enriched"} <= tables


def test_trips_enriched_dedupes_by_most_recent_synced_at(memory_conn):
    _insert_trip(memory_conn, ride_id="r1", period="2020-01", synced_at="2020-02-01T00:00:00")
    _insert_trip(memory_conn, ride_id="r1", period="2020-02", synced_at="2020-03-01T00:00:00")
    memory_conn.commit()
    refresh_trips_enriched(memory_conn)

    rows = memory_conn.execute(
        "SELECT period FROM trips_enriched WHERE ride_id = 'r1'"
    ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "2020-02"


def test_trips_enriched_passes_through_null_ride_ids_unfiltered(memory_conn):
    _insert_trip(memory_conn, ride_id=None)
    _insert_trip(memory_conn, ride_id=None)
    memory_conn.commit()
    refresh_trips_enriched(memory_conn)

    rows = memory_conn.execute(
        "SELECT COUNT(*) FROM trips_enriched WHERE ride_id IS NULL"
    ).fetchone()

    assert rows[0] == 2


def test_trips_enriched_prefers_live_station_coordinates(memory_conn):
    memory_conn.execute(
        "INSERT INTO stations (station_id, short_name, name, lat, lon, capacity, updated_at) "
        "VALUES ('S1', 'S1', 'Live Name', 38.9, -77.0, 15, 'now')"
    )
    _insert_trip(
        memory_conn,
        ride_id="r2",
        start_station_id="S1",
        start_lat=38.5,
        start_lng=-76.5,
    )
    memory_conn.commit()
    refresh_trips_enriched(memory_conn)

    row = memory_conn.execute(
        "SELECT start_station_name, start_lat, start_lng, start_station_capacity "
        "FROM trips_enriched WHERE ride_id = 'r2'"
    ).fetchone()

    assert row == ("Live Name", 38.9, -77.0, 15)


def test_trips_enriched_joins_via_station_short_name(memory_conn):
    # GBFS station_id is a UUID; historical trip data references the legacy
    # numeric short_name instead, so the join must match on either.
    memory_conn.execute(
        "INSERT INTO stations (station_id, short_name, name, lat, lon, capacity, updated_at) "
        "VALUES ('08263fbd-uuid', '31104', 'Legacy Numbered Station', 38.9, -77.0, 19, 'now')"
    )
    _insert_trip(memory_conn, ride_id="r4", start_station_id="31104")
    memory_conn.commit()
    refresh_trips_enriched(memory_conn)

    row = memory_conn.execute(
        "SELECT start_station_name FROM trips_enriched WHERE ride_id = 'r4'"
    ).fetchone()

    assert row == ("Legacy Numbered Station",)


def test_trips_enriched_table_is_empty_until_refreshed(memory_conn):
    _insert_trip(memory_conn, ride_id="r5")
    memory_conn.commit()

    count = memory_conn.execute("SELECT COUNT(*) FROM trips_enriched").fetchone()[0]
    assert count == 0

    refresh_trips_enriched(memory_conn)

    count = memory_conn.execute("SELECT COUNT(*) FROM trips_enriched").fetchone()[0]
    assert count == 1


def test_refresh_trips_enriched_reflects_deletions_and_returns_row_count(memory_conn):
    _insert_trip(memory_conn, ride_id="r6")
    memory_conn.commit()
    assert refresh_trips_enriched(memory_conn) == 1

    memory_conn.execute("DELETE FROM raw_trips WHERE ride_id = 'r6'")
    memory_conn.commit()
    assert refresh_trips_enriched(memory_conn) == 0


def test_trips_enriched_has_expected_indexes(memory_conn):
    index_names = {
        row[0]
        for row in memory_conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'trips_enriched'"
        )
    }
    assert {
        "idx_trips_enriched_ride_id",
        "idx_trips_enriched_period",
        "idx_trips_enriched_start_station",
        "idx_trips_enriched_end_station",
        "idx_trips_enriched_started_at",
    } <= index_names


def test_trips_enriched_falls_back_to_trip_coordinates_when_station_missing(memory_conn):
    _insert_trip(
        memory_conn,
        ride_id="r3",
        start_station_id="UNKNOWN",
        start_lat=38.5,
        start_lng=-76.5,
    )
    memory_conn.commit()
    refresh_trips_enriched(memory_conn)

    row = memory_conn.execute(
        "SELECT start_lat, start_lng FROM trips_enriched WHERE ride_id = 'r3'"
    ).fetchone()

    assert row == (38.5, -76.5)
