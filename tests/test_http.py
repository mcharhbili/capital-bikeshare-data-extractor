import responses

from capital_bikeshare_extractor.http import SESSION


def test_session_has_retry_mounted_on_both_schemes():
    http_adapter = SESSION.get_adapter("http://example.com")
    https_adapter = SESSION.get_adapter("https://example.com")

    assert http_adapter.max_retries.total == 5
    assert https_adapter.max_retries.total == 5
    assert 503 in http_adapter.max_retries.status_forcelist
    assert 429 in http_adapter.max_retries.status_forcelist


@responses.activate
def test_session_retries_transient_failure_then_succeeds():
    url = "https://example.com/resource"
    responses.add(responses.GET, url, status=503)
    responses.add(responses.GET, url, status=200, body="ok")

    response = SESSION.get(url)

    assert response.status_code == 200
    assert response.text == "ok"
