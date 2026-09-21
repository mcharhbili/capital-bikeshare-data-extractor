import sqlite3

from capital_bikeshare_extractor.schema import init_db


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
    assert {"raw_trips", "stations"} <= tables
