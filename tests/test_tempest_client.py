"""Tempest client tests — REST + UDP parsers, error paths."""

from __future__ import annotations

import json
import socket

import pytest
import requests_mock

from tempest_mesh.tempest_client import (
    TempestClient,
    TempestFetchError,
    _obs_from_cloud_payload,
    _obs_from_udp_packet,
)


def test_cloud_payload_parsed(cloud_observation_payload):
    obs = _obs_from_cloud_payload(cloud_observation_payload)
    # 24.4°C ≈ 75.92°F
    assert obs.temperature_c == pytest.approx(24.4)
    assert obs.temperature_f == pytest.approx(75.92, abs=0.05)
    assert obs.humidity == 22
    # 2.5 m/s wind -> ≈ 5.59 mph
    assert obs.wind_avg_mph == pytest.approx(5.59, abs=0.05)
    assert obs.wind_direction == 170
    # 970.4 mb -> 28.66 inHg
    assert obs.pressure_inhg == pytest.approx(28.66, abs=0.05)
    assert obs.uv_index == 6


def test_cloud_payload_empty_raises():
    with pytest.raises(TempestFetchError):
        _obs_from_cloud_payload({"obs": []})


def test_cloud_fetch_via_client(fixed_glendale_config, cloud_observation_payload):
    with requests_mock.Mocker() as m:
        m.get(
            "https://swd.weatherflow.com/swd/rest/observations/station/12345",
            json=cloud_observation_payload,
        )
        client = TempestClient(fixed_glendale_config)
        obs = client.fetch()
        assert obs.temperature_f is not None
        assert m.last_request.qs["token"] == ["test-token"]


def test_cloud_fetch_401_message(fixed_glendale_config):
    with requests_mock.Mocker() as m:
        m.get(
            "https://swd.weatherflow.com/swd/rest/observations/station/12345",
            status_code=401,
            text="unauthorized",
        )
        client = TempestClient(fixed_glendale_config)
        with pytest.raises(TempestFetchError, match="TEMPEST_API_TOKEN"):
            client.fetch()


def test_cloud_fetch_404_message(fixed_glendale_config):
    with requests_mock.Mocker() as m:
        m.get(
            "https://swd.weatherflow.com/swd/rest/observations/station/12345",
            status_code=404,
        )
        client = TempestClient(fixed_glendale_config)
        with pytest.raises(TempestFetchError, match="TEMPEST_STATION_ID"):
            client.fetch()


def test_cloud_fetch_requires_token_and_station(fixed_glendale_config):
    fixed_glendale_config.tempest.api_token = ""
    client = TempestClient(fixed_glendale_config)
    with pytest.raises(TempestFetchError, match="required"):
        client.fetch()


def test_udp_packet_parsed(udp_obs_st_packet):
    obs = _obs_from_udp_packet(udp_obs_st_packet)
    assert obs.temperature_c == pytest.approx(24.4)
    assert obs.temperature_f == pytest.approx(75.92, abs=0.05)
    assert obs.wind_direction == 170
    assert obs.illuminance == 60000
    assert obs.uv_index == 6


def test_udp_packet_wrong_type_raises():
    with pytest.raises(TempestFetchError, match="unexpected UDP packet type"):
        _obs_from_udp_packet({"type": "rapid_wind"})


def test_udp_packet_missing_obs_raises():
    with pytest.raises(TempestFetchError, match="no obs"):
        _obs_from_udp_packet({"type": "obs_st", "obs": []})


def test_udp_fetch_returns_obs_on_packet(fixed_glendale_config, udp_obs_st_packet, monkeypatch):
    fixed_glendale_config.tempest.source = "udp"
    fixed_glendale_config.tempest.udp_listen_seconds = 1
    fixed_glendale_config.tempest.udp_port = 50299

    class FakeSocket:
        def __init__(self, *_, **__):
            self._sent = False

        def setsockopt(self, *args, **kwargs):
            pass

        def bind(self, addr):
            pass

        def settimeout(self, t):
            pass

        def recvfrom(self, size):
            self._sent = True
            return (json.dumps(udp_obs_st_packet).encode(), ("10.0.0.1", 50222))

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", FakeSocket)
    client = TempestClient(fixed_glendale_config)
    obs = client.fetch()
    assert obs.temperature_c == pytest.approx(24.4)


def test_udp_fetch_timeout_raises(fixed_glendale_config, monkeypatch):
    fixed_glendale_config.tempest.source = "udp"

    class TimeoutSocket:
        def __init__(self, *_, **__):
            pass

        def setsockopt(self, *args, **kwargs):
            pass

        def bind(self, addr):
            pass

        def settimeout(self, t):
            pass

        def recvfrom(self, size):
            raise TimeoutError()

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", TimeoutSocket)
    client = TempestClient(fixed_glendale_config)
    with pytest.raises(TempestFetchError, match="no obs_st packet"):
        client.fetch()


def test_garden_api_fetch(fixed_glendale_config):
    fixed_glendale_config.tempest.source = "garden_api"
    fixed_glendale_config.tempest.garden_api_url = "http://localhost:3402"
    fixed_glendale_config.tempest.local_token = "shared-secret"
    with requests_mock.Mocker() as m:
        m.get(
            "http://localhost:3402/api/sensors/tempest-local",
            json={
                "receiving": True,
                "observation": {
                    "temperature_f": 75.0,
                    "humidity": 30,
                    "wind_avg_mph": 5,
                    "wind_direction": 90,
                    "uv_index": 4,
                },
            },
        )
        client = TempestClient(fixed_glendale_config)
        obs = client.fetch()
        assert obs.temperature_f == 75.0
        # Token header was sent
        assert m.last_request.headers["X-Tempest-Token"] == "shared-secret"


def test_garden_api_requires_url(fixed_glendale_config):
    fixed_glendale_config.tempest.source = "garden_api"
    fixed_glendale_config.tempest.garden_api_url = ""
    client = TempestClient(fixed_glendale_config)
    with pytest.raises(TempestFetchError, match="GARDEN_API_URL"):
        client.fetch()


def test_unknown_source_raises(fixed_glendale_config):
    fixed_glendale_config.tempest.source = "wrong"  # type: ignore[assignment]
    client = TempestClient(fixed_glendale_config)
    with pytest.raises(TempestFetchError, match="unknown tempest source"):
        client.fetch()
