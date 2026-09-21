import pytest

from capital_bikeshare_extractor.config import load_config


def test_load_config_has_required_keys(config):
    assert config.s3_list_url.startswith("https://")
    assert config.gbfs_station_information_url.startswith("https://")
    assert "print" in config.supported_output_formats
    assert ".db" in config.backend_extensions
    assert "Start date" in config.column_renames
    assert config.column_renames["Start date"] == "started_at"


def test_load_config_strips_whitespace_from_header_keys(config):
    # "Duration" (no trailing space) must be present, and lookups are
    # resilient to header whitespace via normalize.py's own stripping,
    # not by encoding whitespace variants into the YAML.
    assert config.column_renames["Duration"] == "duration"


def test_load_config_malformed_yaml_raises(monkeypatch):
    import capital_bikeshare_extractor.config as config_mod

    monkeypatch.setattr(config_mod, "_read_yaml_text", lambda: "not: valid: yaml: [")
    with pytest.raises(ValueError):
        config_mod.load_config()


def test_load_config_missing_required_key_raises(monkeypatch):
    import capital_bikeshare_extractor.config as config_mod

    monkeypatch.setattr(config_mod, "_read_yaml_text", lambda: "s3_list_url: 'x'")
    with pytest.raises(ValueError):
        config_mod.load_config()
