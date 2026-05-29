"""Config loader tests."""

from __future__ import annotations

import pytest

from tempest_mesh.config import AppConfig, load_config


def test_defaults_are_valid():
    cfg = AppConfig()
    assert cfg.tempest.source == "cloud"
    assert cfg.meshmonitor.url == "http://localhost:8080"
    assert cfg.location.strategy == "fixed"
    assert cfg.severe_alerter.enabled is True


def test_load_config_yaml_only(write_config_yaml):
    p = write_config_yaml(
        """
tempest:
  source: udp
  udp_port: 51111
meshmonitor:
  url: http://example.com
  api_token: hardcoded
location:
  strategy: fixed
  name: Anytown
  lat: 10.0
  lon: 20.0
"""
    )
    cfg = load_config(p)
    assert cfg.tempest.source == "udp"
    assert cfg.tempest.udp_port == 51111
    assert cfg.meshmonitor.url == "http://example.com"
    assert cfg.location.name == "Anytown"


def test_env_overrides_yaml(write_config_yaml, monkeypatch):
    p = write_config_yaml(
        """
meshmonitor:
  url: http://from-yaml.test
  api_token: from-yaml
"""
    )
    monkeypatch.setenv("MESHMONITOR_URL", "http://from-env.test")
    monkeypatch.setenv("MESHMONITOR_API_TOKEN", "from-env-token")
    cfg = load_config(p)
    assert cfg.meshmonitor.url == "http://from-env.test"
    assert cfg.meshmonitor.api_token == "from-env-token"


def test_env_var_expansion_in_yaml(write_config_yaml, monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "expanded-value")
    p = write_config_yaml(
        """
meshmonitor:
  api_token: "${MY_TOKEN}"
"""
    )
    cfg = load_config(p)
    assert cfg.meshmonitor.api_token == "expanded-value"


def test_missing_env_var_expands_to_empty(write_config_yaml, monkeypatch):
    monkeypatch.delenv("DEFINITELY_NOT_SET", raising=False)
    p = write_config_yaml(
        """
meshmonitor:
  api_token: "${DEFINITELY_NOT_SET}"
"""
    )
    cfg = load_config(p)
    assert cfg.meshmonitor.api_token == ""


def test_station_id_coerced_to_string():
    from tempest_mesh.config import TempestConfig

    tc = TempestConfig(station_id=12345)  # int input
    assert tc.station_id == "12345"


def test_unknown_top_level_key_rejected(write_config_yaml):
    p = write_config_yaml(
        """
mystery: 1
meshmonitor:
  url: http://x
"""
    )
    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError varies by version
        load_config(p)


def test_load_config_without_file_uses_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MESHMONITOR_URL", "http://env-only.test")
    cfg = load_config(None)
    assert cfg.meshmonitor.url == "http://env-only.test"
