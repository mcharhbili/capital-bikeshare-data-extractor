import json

from capital_bikeshare_extractor import cli


def test_sync_period_dispatches_to_core_with_parsed_args(monkeypatch, tmp_path, capsys):
    calls = {}

    def fake_sync_period(config, db_path, manifest_path, period, force=False, dry_run=False):
        calls["args"] = (period, force, dry_run)
        return cli.core.SyncResult(period=period, status="Completed", rows_deleted=1, rows_inserted=2)

    monkeypatch.setattr(cli.core, "sync_period", fake_sync_period)

    exit_code = cli.main(
        [
            "--db",
            str(tmp_path / "db.sqlite"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--output-format",
            "json",
            "sync-period",
            "2023-01",
            "--force",
        ]
    )

    assert exit_code == 0
    assert calls["args"] == ("2023-01", True, False)

    output = json.loads(capsys.readouterr().out)
    assert output["period"] == "2023-01"
    assert output["status"] == "Completed"
    assert output["rows_inserted"] == 2


def test_dry_run_prevents_mutating_call(monkeypatch, tmp_path, capsys):
    called = {"value": False}

    def fake_sync_period(config, db_path, manifest_path, period, force=False, dry_run=False):
        called["value"] = True
        assert dry_run is True
        return cli.core.SyncResult(period=period, status="skipped_dry_run")

    monkeypatch.setattr(cli.core, "sync_period", fake_sync_period)

    exit_code = cli.main(
        [
            "--db",
            str(tmp_path / "db.sqlite"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "sync-period",
            "2023-01",
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert called["value"] is True

    output = capsys.readouterr().out
    assert "skipped_dry_run" in output


def test_plan_dispatches_to_core_plan_sync(monkeypatch, tmp_path, capsys):
    def fake_plan_sync(config, manifest_path, latest_only=False):
        assert latest_only is True
        return ["2023-05"]

    monkeypatch.setattr(cli.core, "plan_sync", fake_plan_sync)

    exit_code = cli.main(
        [
            "--db",
            str(tmp_path / "db.sqlite"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--output-format",
            "json",
            "plan",
            "--latest-only",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["pending"] == ["2023-05"]


def test_output_format_dict_produces_python_repr(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.core, "compute_periods", lambda config: {})

    exit_code = cli.main(
        [
            "--db",
            str(tmp_path / "db.sqlite"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--output-format",
            "dict",
            "periods",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert output.strip() == "[]"


def test_errors_are_reported_and_exit_nonzero(monkeypatch, tmp_path, capsys):
    def fake_sync_period(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli.core, "sync_period", fake_sync_period)

    exit_code = cli.main(
        [
            "--db",
            str(tmp_path / "db.sqlite"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "sync-period",
            "2023-01",
        ]
    )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "boom" in err
