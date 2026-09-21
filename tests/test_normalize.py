import pandas as pd
import pytest

from capital_bikeshare_extractor.normalize import normalize_dataframe
from capital_bikeshare_extractor.schema import RAW_TRIPS_COLUMNS
from capital_bikeshare_extractor.validation import UnrecognizedColumnsError


def test_normalize_modern_headers_passthrough(config):
    df = pd.DataFrame(
        {
            "ride_id": ["r1"],
            "rideable_type": ["classic_bike"],
            "started_at": ["2023-01-01 00:00:00"],
            "ended_at": ["2023-01-01 00:10:00"],
            "start_station_name": ["A"],
            "start_station_id": ["1"],
            "end_station_name": ["B"],
            "end_station_id": ["2"],
            "start_lat": [38.9],
            "start_lng": [-77.0],
            "end_lat": [38.91],
            "end_lng": [-77.01],
            "member_casual": ["member"],
        }
    )
    result = normalize_dataframe(
        df, config.column_renames, period="2023-01", source_key="k", synced_at="now"
    )

    assert list(result.columns) == RAW_TRIPS_COLUMNS
    assert result.loc[0, "ride_id"] == "r1"
    assert result.loc[0, "period"] == "2023-01"
    assert result.loc[0, "source_key"] == "k"


def test_normalize_legacy_headers_remapped(config):
    df = pd.DataFrame(
        {
            "Duration": [600],
            "Start date": ["2015-01-01 00:00:00"],
            "End date": ["2015-01-01 00:10:00"],
            "Start station number": ["1"],
            "Start station": ["A"],
            "End station number": ["2"],
            "End station": ["B"],
            "Bike number": ["W123"],
            "Member type": ["Member"],
        }
    )
    result = normalize_dataframe(
        df, config.column_renames, period="2015", source_key="k", synced_at="now"
    )

    assert result.loc[0, "duration"] == 600
    assert result.loc[0, "start_station_name"] == "A"
    assert result.loc[0, "member_casual"] == "Member"
    # ride_id doesn't exist pre-2020 -- expected structural gap, not an error
    assert pd.isna(result.loc[0, "ride_id"])


def test_normalize_handles_trailing_whitespace_headers(config):
    df = pd.DataFrame(
        {
            "Duration ": [600],
            "Start date": ["2015-01-01 00:00:00"],
            "End date": ["2015-01-01 00:10:00"],
        }
    )
    result = normalize_dataframe(
        df, config.column_renames, period="2015", source_key="k", synced_at="now"
    )

    assert result.loc[0, "duration"] == 600


def test_normalize_raises_on_unrecognized_column(config):
    df = pd.DataFrame(
        {
            "started_at": ["2023-01-01"],
            "ended_at": ["2023-01-01"],
            "Totally Unexpected Column": ["???"],
        }
    )
    with pytest.raises(UnrecognizedColumnsError) as exc_info:
        normalize_dataframe(
            df, config.column_renames, period="2023-01", source_key="mykey.zip", synced_at="now"
        )

    assert "Totally Unexpected Column" in str(exc_info.value)
    assert "mykey.zip" in str(exc_info.value)
