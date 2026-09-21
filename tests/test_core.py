from pathlib import Path

from capital_bikeshare_extractor import core
from capital_bikeshare_extractor.manifest import ManifestData, ManifestEntry, STATUS_NEW, save


def _seed_manifest(manifest_path, period, key="k.zip"):
    data = ManifestData(
        files={period: ManifestEntry(key=key, period=period, size=1, status=STATUS_NEW, etag="e")}
    )
    save(manifest_path, data, "2024-01-01T00:00:00")
    return data


class _FakeStore:
    """No-op Store stand-in so core tests don't need a real backend."""

    def init(self):
        pass

    def load_period(self, config, csv_paths, period, source_key, synced_at):
        return (0, len(csv_paths))

    def sync_stations(self, config, synced_at):
        return 0


def _patch_pipeline(monkeypatch, tmp_path, period, key):
    """Stub download/extract so sync_period runs without network or a real DB,
    while still creating the .temp files cleanup is expected to remove."""
    zip_path = tmp_path / ".temp" / key
    extract_dir = tmp_path / ".temp" / period

    def fake_download_zip(config, key_, temp_dir, force=False):
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(b"fake zip")
        return zip_path

    def fake_extract_csvs(zip_path_, extract_dir_, force=False):
        extract_dir.mkdir(parents=True, exist_ok=True)
        csv_path = extract_dir / "trips.csv"
        csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
        return [csv_path]

    monkeypatch.setattr(core, "download_zip", fake_download_zip)
    monkeypatch.setattr(core, "extract_csvs", fake_extract_csvs)
    monkeypatch.setattr(core, "DEFAULT_TEMP_DIR", tmp_path / ".temp")

    return zip_path, extract_dir


def test_sync_period_cleans_up_its_own_temp_files(monkeypatch, tmp_path, config):
    period = "2023-01"
    key = "k.zip"
    manifest_path = tmp_path / "manifest.json"
    _seed_manifest(manifest_path, period, key)

    zip_path, extract_dir = _patch_pipeline(monkeypatch, tmp_path, period, key)

    result = core.sync_period(config, _FakeStore(), manifest_path, period)

    assert result.status == "Completed"
    assert not zip_path.exists()
    assert not extract_dir.exists()


def test_sync_pending_sweeps_temp_dir_when_done(monkeypatch, tmp_path, config):
    period = "2023-01"
    key = "k.zip"
    manifest_path = tmp_path / "manifest.json"
    _seed_manifest(manifest_path, period, key)

    temp_dir = tmp_path / ".temp"
    _patch_pipeline(monkeypatch, tmp_path, period, key)

    def fake_refresh_manifest(config_, manifest_path_):
        data = core.manifest_mod.load(manifest_path_)

        class _Result:
            pending = [period]

        return data, _Result()

    monkeypatch.setattr(core, "refresh_manifest", fake_refresh_manifest)

    # Leave a stray file behind to simulate leftovers from a prior interrupted run.
    stray_dir = temp_dir / "leftover"
    stray_dir.mkdir(parents=True, exist_ok=True)
    (stray_dir / "stray.txt").write_text("x", encoding="utf-8")

    core.sync_pending(config, _FakeStore(), manifest_path)

    assert not temp_dir.exists()
