import pytest
import responses

from capital_bikeshare_extractor.s3_discovery import (
    classify_key,
    discover_periods,
    list_s3_objects,
    parse_list_bucket_result,
)

NS = "http://s3.amazonaws.com/doc/2006-03-01/"


def _page_xml(keys_sizes_etags, is_truncated=False, next_token=None):
    contents = "".join(
        f"""
        <Contents>
            <Key>{key}</Key>
            <Size>{size}</Size>
            <ETag>&quot;{etag}&quot;</ETag>
        </Contents>
        """
        for key, size, etag in keys_sizes_etags
    )
    truncated = "true" if is_truncated else "false"
    token_xml = f"<NextContinuationToken>{next_token}</NextContinuationToken>" if next_token else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <ListBucketResult xmlns="{NS}">
        <IsTruncated>{truncated}</IsTruncated>
        {token_xml}
        {contents}
    </ListBucketResult>""".encode("utf-8")


@pytest.mark.parametrize(
    "key,expected_period",
    [
        ("2016-capitalbikeshare-tripdata.zip", "2016"),
        ("2010-capitalbikeshare-tripdata.zip", "2010"),
        ("201801-capitalbikeshare-tripdata.zip", "2018-01"),
        ("202312-capitalbikeshare-tripdata.zip", "2023-12"),
        ("readme.txt", None),
        ("2018Q1-capitalbikeshare-tripdata.zip", None),
        ("random-file.zip", None),
    ],
)
def test_classify_key(key, expected_period):
    assert classify_key(key) == expected_period


def test_parse_list_bucket_result_single_page():
    xml_bytes = _page_xml([("2016-capitalbikeshare-tripdata.zip", 100, "abc")])
    objects, is_truncated, token = parse_list_bucket_result(xml_bytes)

    assert len(objects) == 1
    assert objects[0].key == "2016-capitalbikeshare-tripdata.zip"
    assert objects[0].size == 100
    assert objects[0].etag == '"abc"'
    assert is_truncated is False
    assert token is None


def test_parse_list_bucket_result_truncated_page():
    xml_bytes = _page_xml(
        [("201801-capitalbikeshare-tripdata.zip", 200, "def")],
        is_truncated=True,
        next_token="page2token",
    )
    objects, is_truncated, token = parse_list_bucket_result(xml_bytes)

    assert len(objects) == 1
    assert is_truncated is True
    assert token == "page2token"


@responses.activate
def test_list_s3_objects_follows_pagination(config):
    page1 = _page_xml(
        [("2016-capitalbikeshare-tripdata.zip", 100, "a")],
        is_truncated=True,
        next_token="tok1",
    )
    page2 = _page_xml([("201801-capitalbikeshare-tripdata.zip", 200, "b")])

    responses.add(responses.GET, config.s3_list_url, body=page1, status=200)
    responses.add(
        responses.GET,
        f"{config.s3_list_url}&continuation-token=tok1",
        body=page2,
        status=200,
    )

    objects = list_s3_objects(config)

    assert {o.key for o in objects} == {
        "2016-capitalbikeshare-tripdata.zip",
        "201801-capitalbikeshare-tripdata.zip",
    }


@responses.activate
def test_discover_periods_surfaces_unclassified_keys(config):
    page = _page_xml(
        [
            ("2016-capitalbikeshare-tripdata.zip", 100, "a"),
            ("some-other-file.txt", 5, "z"),
        ]
    )
    responses.add(responses.GET, config.s3_list_url, body=page, status=200)

    periods, unclassified = discover_periods(config)

    assert "2016" in periods
    assert periods["2016"].key == "2016-capitalbikeshare-tripdata.zip"
    assert "some-other-file.txt" in unclassified
