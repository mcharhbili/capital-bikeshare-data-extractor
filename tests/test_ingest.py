import pytest

from capital_bikeshare_extractor.ingest import load_period
from capital_bikeshare_extractor.validation import UnrecognizedColumnsError


def _write_csv(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_load_period_is_idempotent_on_resync(memory_conn, config, tmp_path):
    csv_path = _write_csv(
        tmp_path,
        "trips.csv",
        "ride_id,rideable_type,started_at,ended_at,start_station_name,start_station_id,"
        "end_station_name,end_station_id,start_lat,start_lng,end_lat,end_lng,member_casual\n"
        "r1,classic_bike,2023-01-01 00:00:00,2023-01-01 00:10:00,A,1,B,2,38.9,-77.0,38.91,-77.01,member\n"
        "r2,classic_bike,2023-01-02 00:00:00,2023-01-02 00:10:00,A,1,B,2,38.9,-77.0,38.91,-77.01,casual\n",
    )

    rows_deleted_1, rows_inserted_1 = load_period(
        memory_conn, config, [csv_path], period="2023-01", source_key="k", synced_at="t1"
    )
    count_after_first = memory_conn.execute(
        "SELECT COUNT(*) FROM raw_trips WHERE period = '2023-01'"
    ).fetchone()[0]

    rows_deleted_2, rows_inserted_2 = load_period(
        memory_conn, config, [csv_path], period="2023-01", source_key="k", synced_at="t2"
    )
    count_after_second = memory_conn.execute(
        "SELECT COUNT(*) FROM raw_trips WHERE period = '2023-01'"
    ).fetchone()[0]

    assert rows_deleted_1 == 0
    assert rows_inserted_1 == 2
    assert count_after_first == 2

    assert rows_deleted_2 == 2
    assert rows_inserted_2 == 2
    assert count_after_second == 2  # not doubled


def test_load_period_rolls_back_on_normalize_failure(memory_conn, config, tmp_path):
    csv_path = _write_csv(
        tmp_path,
        "bad.csv",
        "started_at,ended_at,Totally Unexpected Column\n"
        "2023-01-01,2023-01-01,???\n",
    )

    with pytest.raises(UnrecognizedColumnsError):
        load_period(
            memory_conn, config, [csv_path], period="2023-01", source_key="bad.zip", synced_at="t1"
        )

    count = memory_conn.execute(
        "SELECT COUNT(*) FROM raw_trips WHERE period = '2023-01'"
    ).fetchone()[0]
    assert count == 0
