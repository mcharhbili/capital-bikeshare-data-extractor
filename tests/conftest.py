import sqlite3

import pytest

from capital_bikeshare_extractor.config import load_config
from capital_bikeshare_extractor.schema import init_db


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def memory_conn():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()
