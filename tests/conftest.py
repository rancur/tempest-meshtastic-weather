"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tempest_mesh.config import AppConfig


@pytest.fixture
def minimal_config() -> AppConfig:
    """Returns a fully-defaulted config; tweak attrs in individual tests."""
    return AppConfig()


@pytest.fixture
def fixed_glendale_config() -> AppConfig:
    """Fixed-location config wired to a stub MeshMonitor + cloud Tempest."""
    cfg = AppConfig()
    cfg.tempest.source = "cloud"
    cfg.tempest.api_token = "test-token"
    cfg.tempest.station_id = "12345"
    cfg.meshmonitor.url = "http://meshmonitor.test"
    cfg.meshmonitor.api_token = "mm-test-token"
    cfg.meshmonitor.channel_index = 0
    cfg.meshmonitor.retry_sleep_seconds = 0  # no test delays
    cfg.location.strategy = "fixed"
    cfg.location.name = "Testville"
    cfg.location.lat = 33.5
    cfg.location.lon = -112.2
    cfg.severe_alerter.enabled = True
    cfg.severe_alerter.nws_user_agent = "tempest-mesh-tests (test@example.com)"
    return cfg


@pytest.fixture
def cloud_observation_payload() -> dict[str, Any]:
    """A representative WeatherFlow REST response."""
    return {
        "status": {"status_code": 0, "status_message": "SUCCESS"},
        "station_id": 12345,
        "station_name": "Test Station",
        "elevation": 360.0,
        "obs": [
            {
                "timestamp": 1717000000,
                "air_temperature": 24.4,
                "relative_humidity": 22,
                "barometric_pressure": 1011.2,
                "station_pressure": 970.4,
                "sea_level_pressure": 1011.2,
                "wind_avg": 2.5,
                "wind_direction": 170,
                "wind_gust": 4.1,
                "uv": 6,
                "solar_radiation": 700,
                "brightness": 60000,
                "precip_accum_local_day": 0.0,
                "precip_minute_local": 0.0,
                "lightning_strike_count_last_3hr": 0,
            }
        ],
    }


@pytest.fixture
def udp_obs_st_packet() -> dict[str, Any]:
    """Tempest UDP obs_st packet."""
    return {
        "serial_number": "ST-00000000",
        "type": "obs_st",
        "hub_sn": "HB-00000000",
        "obs": [
            [
                1717000000,  # epoch
                1.3,  # wind_lull (m/s)
                2.5,  # wind_avg (m/s)
                4.1,  # wind_gust (m/s)
                170,  # wind_direction
                3,  # wind_sample_interval
                970.4,  # pressure (mb)
                24.4,  # air_temperature (C)
                22,  # relative_humidity
                60000,  # illuminance
                6,  # uv
                700,  # solar_radiation
                0.0,  # rain_accumulated (mm)
                0,  # precipitation_type
                0,  # avg strike distance
                0,  # strike count
                3.30,  # battery
                1,  # report_interval
            ]
        ],
        "firmware_revision": 156,
    }


@pytest.fixture
def open_meteo_forecast() -> dict[str, Any]:
    """Open-Meteo hourly forecast with rain starting in hour +3."""
    from datetime import datetime, timedelta

    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    times = [(now + timedelta(hours=h)).isoformat() for h in range(0, 24)]
    precip = [0.0, 0.0, 0.0, 3.0, 1.5, 0.5, 0.0, 0.0] + [0.0] * 16
    codes = [0, 0, 0, 65, 63, 61, 0, 0] + [0] * 16
    return {
        "hourly": {
            "time": times,
            "weather_code": codes,
            "wind_speed_10m": [10] * 24,
            "wind_gusts_10m": [15] * 24,
            "precipitation": precip,
            "temperature_2m": [70] * 24,
        }
    }


@pytest.fixture
def nws_active_alerts_payload() -> dict[str, Any]:
    """Mocked NWS /alerts/active feature collection with one severe alert."""
    return {
        "features": [
            {
                "id": "urn:oid:test-1",
                "properties": {
                    "id": "urn:oid:test-1",
                    "event": "Severe Thunderstorm Warning",
                    "headline": "Severe Thunderstorm Warning ...Damaging winds and large hail expected",
                    "severity": "Severe",
                    "expires": "2026-05-29T18:30:00+00:00",
                },
            },
            {
                # Minor alert — should be filtered out.
                "id": "urn:oid:test-2",
                "properties": {
                    "id": "urn:oid:test-2",
                    "event": "Special Weather Statement",
                    "severity": "Minor",
                    "expires": "2026-05-29T18:30:00+00:00",
                },
            },
        ]
    }


@pytest.fixture
def write_config_yaml(tmp_path: Path):
    """Factory: write a config.yaml in tmp_path."""

    def _write(body: str) -> Path:
        p = tmp_path / "config.yaml"
        p.write_text(body)
        return p

    return _write
