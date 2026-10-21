"""Conditions poster tests — location resolution + end-to-end post."""

from __future__ import annotations

import time

import requests_mock

from tempest_mesh.conditions_poster import post_current_conditions, resolve_location
from tempest_mesh.meshmonitor_client import MeshMonitorClient


def test_resolve_location_fixed_returns_name(fixed_location_config):
    mm = MeshMonitorClient(fixed_location_config)
    name, reason = resolve_location(fixed_location_config, mm)
    assert name == "Testville"
    assert reason is None


def test_resolve_location_from_meshmonitor_stale_position(fixed_location_config):
    fixed_location_config.location.strategy = "from_meshmonitor"
    fixed_location_config.location.meshmonitor_node_id = "!abcd"
    fixed_location_config.location.position_max_age_seconds = 60
    with requests_mock.Mocker() as m:
        m.get(
            "http://meshmonitor.test/api/v1/nodes",
            json={
                "data": [
                    {
                        "nodeId": "!abcd",
                        "latitude": 10.0,
                        "longitude": 20.0,
                        "role": 0,
                        "hopsAway": None,
                        "positionTimestamp": int((time.time() - 7200) * 1000),
                    }
                ]
            },
        )
        mm = MeshMonitorClient(fixed_location_config)
        name, reason = resolve_location(fixed_location_config, mm)
        assert reason is not None
        assert reason.startswith("stale_position_age_")


def test_post_current_conditions_dry_run(fixed_location_config, cloud_observation_payload, capsys):
    with requests_mock.Mocker() as m:
        m.get(
            "https://swd.weatherflow.com/swd/rest/observations/station/12345",
            json=cloud_observation_payload,
        )
        result = post_current_conditions(fixed_location_config, dry_run=True)
        assert result.success is True
        captured = capsys.readouterr()
        assert "WX Testville" in captured.out


def test_post_current_conditions_real_post(fixed_location_config, cloud_observation_payload):
    with requests_mock.Mocker() as m:
        m.get(
            "https://swd.weatherflow.com/swd/rest/observations/station/12345",
            json=cloud_observation_payload,
        )
        m.post("http://meshmonitor.test/api/v1/messages", json={"success": True})
        result = post_current_conditions(fixed_location_config)
        assert result.success is True
        body = m.last_request.json()
        assert "WX Testville" in body["text"]


def test_post_current_conditions_meshmonitor_failure(
    fixed_location_config, cloud_observation_payload
):
    with requests_mock.Mocker() as m:
        m.get(
            "https://swd.weatherflow.com/swd/rest/observations/station/12345",
            json=cloud_observation_payload,
        )
        m.post("http://meshmonitor.test/api/v1/messages", status_code=500)
        result = post_current_conditions(fixed_location_config)
        assert result.success is False
        assert result.reason == "meshmonitor_post_failed"


def test_post_current_conditions_skipped_on_stale_position(fixed_location_config):
    fixed_location_config.location.strategy = "from_meshmonitor"
    fixed_location_config.location.meshmonitor_node_id = "!stale"
    fixed_location_config.location.position_max_age_seconds = 1
    with requests_mock.Mocker() as m:
        m.get(
            "http://meshmonitor.test/api/v1/nodes",
            json={
                "data": [
                    {
                        "nodeId": "!stale",
                        "latitude": 1.0,
                        "longitude": 2.0,
                        "role": 0,
                        "hopsAway": None,
                        "positionTimestamp": int((time.time() - 86400) * 1000),
                    }
                ]
            },
        )
        result = post_current_conditions(fixed_location_config)
        assert result.success is False
        assert "stale_position" in (result.reason or "")
