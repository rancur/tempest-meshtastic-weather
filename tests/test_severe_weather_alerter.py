"""Severe-weather alerter tests — NWS dedup, transitions, debounce, suppression."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import requests_mock

from tempest_mesh.severe_weather_alerter import (
    dedup_alerts,
    detect_next_hour_transitions,
    evaluate_current_rain,
    evaluate_forecast_lead,
    fetch_nws_alerts,
    run_once,
)


# --- NWS fetch + dedup -------------------------------------------------------
def test_fetch_nws_alerts_filters_minor(nws_active_alerts_payload):
    with requests_mock.Mocker() as m:
        m.get(
            "https://api.weather.gov/alerts/active",
            json=nws_active_alerts_payload,
        )
        out = fetch_nws_alerts(33.5, -112.2, "test-agent (me@example.com)")
        # Only the severe one
        assert len(out) == 1
        assert out[0]["event"] == "Severe Thunderstorm Warning"


def test_fetch_nws_alerts_handles_500():
    with requests_mock.Mocker() as m:
        m.get("https://api.weather.gov/alerts/active", status_code=500)
        out = fetch_nws_alerts(33.5, -112.2, "test")
        assert out == []


def test_fetch_nws_alerts_handles_network_error():
    """If NWS connection drops, we return [] rather than crash the whole run."""
    with requests_mock.Mocker() as m:
        import requests

        m.get(
            "https://api.weather.gov/alerts/active",
            exc=requests.ConnectionError,
        )
        assert fetch_nws_alerts(33.5, -112.2, "test") == []


def test_dedup_alerts_first_time(tmp_path):
    path = tmp_path / "dedup.json"
    alerts = [{"id": "a1", "event": "X"}, {"id": "a2", "event": "Y"}]
    out = dedup_alerts(alerts, path, ttl_hours=6)
    assert len(out) == 2
    assert path.exists()


def test_dedup_alerts_suppresses_repeats(tmp_path):
    path = tmp_path / "dedup.json"
    # First pass — both alerts fresh
    dedup_alerts([{"id": "a1", "event": "X"}], path, ttl_hours=6)
    # Second pass — same id, should be suppressed
    out = dedup_alerts([{"id": "a1", "event": "X"}], path, ttl_hours=6)
    assert out == []


def test_dedup_alerts_ttl_expiry(tmp_path):
    path = tmp_path / "dedup.json"
    # Manually seed an "old" entry
    path.write_text(json.dumps({"seen": {"a1": int(time.time()) - 10 * 3600}}))
    out = dedup_alerts([{"id": "a1", "event": "X"}], path, ttl_hours=6)
    assert len(out) == 1  # 10h > 6h TTL


# --- transition detection ----------------------------------------------------
def test_detect_next_hour_rain_transition():
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    times = [(now + timedelta(hours=h)).isoformat() for h in range(0, 6)]
    forecast = {
        "hourly": {
            "time": times,
            "weather_code": [0, 65, 0, 0, 0, 0],
            "wind_speed_10m": [5, 10, 5, 5, 5, 5],
            "wind_gusts_10m": [10, 15, 10, 10, 10, 10],
            "precipitation": [0.0, 3.0, 0.0, 0.0, 0.0, 0.0],
        }
    }
    alerts, label = detect_next_hour_transitions(forecast, now=now)
    assert "🌧️ Rain" in alerts


def test_detect_next_hour_wind_transition():
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    times = [(now + timedelta(hours=h)).isoformat() for h in range(0, 6)]
    forecast = {
        "hourly": {
            "time": times,
            "weather_code": [0, 0, 0, 0, 0, 0],
            "wind_speed_10m": [5, 25, 5, 5, 5, 5],
            "wind_gusts_10m": [10, 35, 10, 10, 10, 10],
            "precipitation": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        }
    }
    alerts, _ = detect_next_hour_transitions(forecast, now=now)
    assert any("Wind" in a for a in alerts)


def test_detect_next_hour_no_change_returns_empty():
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    times = [(now + timedelta(hours=h)).isoformat() for h in range(0, 6)]
    forecast = {
        "hourly": {
            "time": times,
            "weather_code": [0, 0, 0, 0, 0, 0],
            "wind_speed_10m": [5, 5, 5, 5, 5, 5],
            "wind_gusts_10m": [10, 10, 10, 10, 10, 10],
            "precipitation": [0.0] * 6,
        }
    }
    alerts, _ = detect_next_hour_transitions(forecast, now=now)
    assert alerts == []


# --- current-rain evaluator --------------------------------------------------
def test_current_rain_no_rain_no_broadcast():
    should, _ = evaluate_current_rain(0.0, 0, 1800)
    assert should is False


def test_current_rain_fires_first_time():
    should, fresh = evaluate_current_rain(1.0, 0, 1800)
    assert should is True
    assert fresh is True


def test_current_rain_debounces():
    now = int(time.time())
    should, _ = evaluate_current_rain(1.0, now - 60, 1800)
    assert should is False  # 60s < 1800s debounce


def test_current_rain_sustained_after_debounce():
    now = int(time.time())
    should, fresh = evaluate_current_rain(1.0, now - 3600, 1800, now_ts=now)
    assert should is True
    assert fresh is False  # < 6h gap = sustained, not fresh


def test_current_rain_fresh_after_long_gap():
    now = int(time.time())
    should, fresh = evaluate_current_rain(1.0, now - 8 * 3600, 1800, now_ts=now)
    assert should is True
    assert fresh is True  # > 6h gap = treat as new


# --- forecast lead -----------------------------------------------------------
def test_forecast_lead_finds_rain(open_meteo_forecast):
    out = evaluate_forecast_lead(open_meteo_forecast, min_rain_mm=0.1)
    assert out is not None
    hours_ahead, max_mm = out
    assert hours_ahead >= 1
    assert max_mm >= 3.0


def test_forecast_lead_no_rain():
    forecast = {
        "hourly": {
            "time": [datetime.now().isoformat()],
            "precipitation": [0.0],
        }
    }
    assert evaluate_forecast_lead(forecast, min_rain_mm=0.1) is None


def test_forecast_lead_below_threshold():
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    forecast = {
        "hourly": {
            "time": [(now + timedelta(hours=h)).isoformat() for h in range(0, 6)],
            "precipitation": [0.01, 0.02, 0.03, 0.04, 0.05, 0.0],
        }
    }
    # min threshold is 0.1, all values below
    assert evaluate_forecast_lead(forecast, min_rain_mm=0.1) is None


# --- orchestrator end-to-end ------------------------------------------------
def test_run_once_disabled_returns_skipped(fixed_glendale_config, tmp_path):
    fixed_glendale_config.severe_alerter.enabled = False
    fixed_glendale_config.severe_alerter.state_dir = str(tmp_path)
    result = run_once(fixed_glendale_config, 33.5, -112.2, "X")
    assert result.skipped_reason == "severe_alerter_disabled"


def test_run_once_dry_run_no_post(fixed_glendale_config, tmp_path, nws_active_alerts_payload):
    fixed_glendale_config.severe_alerter.state_dir = str(tmp_path)
    with requests_mock.Mocker() as m:
        m.get("https://api.weather.gov/alerts/active", json=nws_active_alerts_payload)
        # All Open-Meteo requests return "no rain"
        m.get(
            "https://api.open-meteo.com/v1/forecast",
            json={
                "hourly": {"time": [], "precipitation": []},
                "current": {"precipitation": 0},
            },
        )
        result = run_once(fixed_glendale_config, 33.5, -112.2, "Anytown", dry_run=True)
        # NWS alert went through (dry-run still records "sent")
        assert len(result.sent) == 1
        assert "Severe Thunderstorm Warning" in result.sent[0]


def test_run_once_nws_failure_does_not_abort_run(fixed_glendale_config, tmp_path):
    fixed_glendale_config.severe_alerter.state_dir = str(tmp_path)
    with requests_mock.Mocker() as m:
        m.get("https://api.weather.gov/alerts/active", status_code=500)
        m.get(
            "https://api.open-meteo.com/v1/forecast",
            json={
                "hourly": {"time": [], "precipitation": []},
                "current": {"precipitation": 0},
            },
        )
        result = run_once(fixed_glendale_config, 33.5, -112.2, "X", dry_run=True)
        # No exception — quiet pass
        assert result.sent == []
