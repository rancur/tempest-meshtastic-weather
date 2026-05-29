"""MeshMonitor client tests — both message send + node lookup."""

from __future__ import annotations

import time

import pytest
import requests_mock

from tempest_mesh.meshmonitor_client import MeshMonitorClient, MeshMonitorError


def test_post_message_success(fixed_glendale_config):
    with requests_mock.Mocker() as m:
        m.post("http://meshmonitor.test/api/v1/messages", json={"success": True})
        mm = MeshMonitorClient(fixed_glendale_config)
        ok = mm.post_message("hello mesh")
        assert ok is True
        body = m.last_request.json()
        assert body["text"] == "hello mesh"
        assert body["channel"] == 0


def test_post_message_explicit_channel(fixed_glendale_config):
    with requests_mock.Mocker() as m:
        m.post("http://meshmonitor.test/api/v1/messages", json={"success": True})
        mm = MeshMonitorClient(fixed_glendale_config)
        mm.post_message("hi", channel_index=3)
        assert m.last_request.json()["channel"] == 3


def test_post_message_retries_then_succeeds(fixed_glendale_config):
    """Should retry on failure and stop as soon as one attempt returns success."""
    responses = [
        {"status_code": 500, "text": "boom"},
        {"json": {"success": False}},
        {"json": {"success": True}},
    ]
    with requests_mock.Mocker() as m:
        m.post("http://meshmonitor.test/api/v1/messages", responses)
        mm = MeshMonitorClient(fixed_glendale_config)
        ok = mm.post_message("hello", label="retry-test")
        assert ok is True
        assert m.call_count == 3


def test_post_message_gives_up_after_max_attempts(fixed_glendale_config):
    with requests_mock.Mocker() as m:
        m.post("http://meshmonitor.test/api/v1/messages", status_code=500)
        mm = MeshMonitorClient(fixed_glendale_config)
        ok = mm.post_message("never sends")
        assert ok is False
        assert m.call_count == fixed_glendale_config.meshmonitor.retry_max_attempts


def test_post_message_requires_token(fixed_glendale_config):
    fixed_glendale_config.meshmonitor.api_token = ""
    mm = MeshMonitorClient(fixed_glendale_config)
    with pytest.raises(MeshMonitorError, match="MESHMONITOR_API_TOKEN"):
        mm.post_message("x")


def test_list_nodes(fixed_glendale_config):
    with requests_mock.Mocker() as m:
        m.get(
            "http://meshmonitor.test/api/v1/nodes",
            json={
                "data": [
                    {
                        "nodeId": "!abcd1234",
                        "latitude": 33.5,
                        "longitude": -112.2,
                        "role": 0,
                        "hopsAway": None,
                        "positionTimestamp": 1_700_000_000_000,
                        "lastHeard": 1_700_000_000,
                    },
                    {
                        "nodeId": "!9999aaaa",
                        "latitude": None,
                        "longitude": None,
                        "role": 1,
                        "hopsAway": 3,
                        "positionTimestamp": None,
                        "lastHeard": 1_699_000_000,
                    },
                ]
            },
        )
        mm = MeshMonitorClient(fixed_glendale_config)
        nodes = mm.list_nodes()
        assert len(nodes) == 2
        assert nodes[0].node_id == "!abcd1234"
        assert nodes[1].hops_away == 3


def test_resolve_local_node_by_exact_id(fixed_glendale_config):
    with requests_mock.Mocker() as m:
        m.get(
            "http://meshmonitor.test/api/v1/nodes",
            json={
                "data": [
                    {
                        "nodeId": "!aaaaaaaa",
                        "latitude": 1.0,
                        "longitude": 2.0,
                        "role": 1,
                        "hopsAway": 2,
                        "positionTimestamp": int(time.time() * 1000),
                    },
                    {
                        "nodeId": "!abcd1234",
                        "latitude": 33.5,
                        "longitude": -112.2,
                        "role": 0,
                        "hopsAway": None,
                        "positionTimestamp": int(time.time() * 1000),
                    },
                ]
            },
        )
        mm = MeshMonitorClient(fixed_glendale_config)
        pos = mm.resolve_local_node_position("!abcd1234")
        assert pos is not None
        lat, lon, age = pos
        assert lat == 33.5
        assert age < 5  # roughly "now"


def test_resolve_local_node_falls_back_to_local_client(fixed_glendale_config):
    """When the target nodeId isn't present, prefer hopsAway=None + role=0."""
    with requests_mock.Mocker() as m:
        m.get(
            "http://meshmonitor.test/api/v1/nodes",
            json={
                "data": [
                    {
                        "nodeId": "!aaaaaaaa",
                        "latitude": 1.0,
                        "longitude": 2.0,
                        "role": 1,
                        "hopsAway": 5,
                        "positionTimestamp": int(time.time() * 1000),
                    },
                    {
                        "nodeId": "!bbbbbbbb",
                        "latitude": 10.0,
                        "longitude": 20.0,
                        "role": 0,
                        "hopsAway": None,
                        "positionTimestamp": int(time.time() * 1000),
                    },
                ]
            },
        )
        mm = MeshMonitorClient(fixed_glendale_config)
        pos = mm.resolve_local_node_position("!notpresent")
        assert pos is not None
        assert pos[0] == 10.0


def test_resolve_local_node_returns_none_when_no_gps(fixed_glendale_config):
    with requests_mock.Mocker() as m:
        m.get(
            "http://meshmonitor.test/api/v1/nodes",
            json={"data": [{"nodeId": "!x", "latitude": None, "longitude": None}]},
        )
        mm = MeshMonitorClient(fixed_glendale_config)
        assert mm.resolve_local_node_position("") is None
