from capital_bikeshare_extractor import manifest as manifest_mod
from capital_bikeshare_extractor.manifest import (
    ManifestData,
    ManifestEntry,
    STATUS_COMPLETED,
    STATUS_NEW,
    STATUS_UPDATED,
    diff,
    load,
    mark_completed,
    save,
)


class FakePeriodInfo:
    def __init__(self, key, size, etag):
        self.key = key
        self.size = size
        self.etag = etag


def test_diff_all_new_when_manifest_empty():
    fresh = {"2018-01": FakePeriodInfo("k", 100, "etag1")}
    result = diff(fresh, ManifestData())

    assert result.new == ["2018-01"]
    assert result.updated == []
    assert result.completed == []
    assert result.pending == ["2018-01"]


def test_diff_updated_when_etag_changes():
    existing = ManifestData(
        files={
            "2018-01": ManifestEntry(
                key="k", period="2018-01", size=100, status=STATUS_COMPLETED, etag="old"
            )
        }
    )
    fresh = {"2018-01": FakePeriodInfo("k", 100, "new")}

    result = diff(fresh, existing)

    assert result.updated == ["2018-01"]
    assert result.completed == []


def test_diff_stays_completed_when_unchanged():
    existing = ManifestData(
        files={
            "2018-01": ManifestEntry(
                key="k", period="2018-01", size=100, status=STATUS_COMPLETED, etag="same"
            )
        }
    )
    fresh = {"2018-01": FakePeriodInfo("k", 100, "same")}

    result = diff(fresh, existing)

    assert result.completed == ["2018-01"]
    assert result.pending == []


def test_diff_stays_pending_when_unchanged_but_never_completed():
    existing = ManifestData(
        files={
            "2018-01": ManifestEntry(
                key="k", period="2018-01", size=100, status=STATUS_NEW, etag="same"
            )
        }
    )
    fresh = {"2018-01": FakePeriodInfo("k", 100, "same")}

    result = diff(fresh, existing)

    assert result.new == ["2018-01"]
    assert result.pending == ["2018-01"]


def test_diff_ignores_periods_removed_from_s3():
    existing = ManifestData(
        files={
            "2018-01": ManifestEntry(
                key="k", period="2018-01", size=100, status=STATUS_COMPLETED, etag="same"
            )
        }
    )
    result = diff({}, existing)

    assert result.new == []
    assert result.updated == []
    assert result.completed == []


def test_mark_completed_sets_status_and_timestamp():
    data = ManifestData(
        files={
            "2018-01": ManifestEntry(
                key="k", period="2018-01", size=100, status=STATUS_NEW, etag="e"
            )
        }
    )
    mark_completed(data, "2018-01", "2020-01-01T00:00:00")

    entry = data.files["2018-01"]
    assert entry.status == STATUS_COMPLETED
    assert entry.last_synced_at == "2020-01-01T00:00:00"


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "manifest.json"
    data = ManifestData(
        files={
            "2018-01": ManifestEntry(
                key="k", period="2018-01", size=100, status=STATUS_COMPLETED, etag="e",
                last_synced_at="2020-01-01T00:00:00",
            )
        }
    )
    save(path, data, "2020-01-01T00:00:00")

    loaded = load(path)

    assert loaded.files["2018-01"].key == "k"
    assert loaded.files["2018-01"].status == STATUS_COMPLETED
    assert loaded.updated_at == "2020-01-01T00:00:00"


def test_load_missing_file_returns_empty_manifest(tmp_path):
    loaded = load(tmp_path / "does-not-exist.json")
    assert loaded.files == {}
