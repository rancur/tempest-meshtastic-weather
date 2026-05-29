"""Message formatter tests — units, truncation, condition derivation, severe templates."""

from __future__ import annotations

from datetime import datetime

import pytest

from tempest_mesh.config import MessageConfig
from tempest_mesh.message_formatter import (
    _enforce_max_bytes,
    deg_to_compass,
    format_conditions_message,
    format_current_rain_message,
    format_forecast_lead_message,
    format_nws_alert_message,
    format_open_meteo_transition_message,
    format_time,
    get_condition_and_emoji,
)
from tempest_mesh.tempest_client import Observation


# --- deg_to_compass ----------------------------------------------------------
@pytest.mark.parametrize(
    "deg,expected",
    [
        (0, "N"),
        (45, "NE"),
        (90, "E"),
        (180, "S"),
        (270, "W"),
        (359, "N"),
        (22.5, "NNE"),
    ],
)
def test_deg_to_compass(deg, expected):
    assert deg_to_compass(deg) == expected


def test_deg_to_compass_none():
    assert deg_to_compass(None) == ""


def test_deg_to_compass_bad_input():
    assert deg_to_compass("not a number") == ""


# --- format_time --------------------------------------------------------------
@pytest.mark.parametrize(
    "hour,expected",
    [
        (0, "12AM"),
        (1, "1AM"),
        (11, "11AM"),
        (12, "12PM"),
        (13, "1PM"),
        (23, "11PM"),
    ],
)
def test_format_time(hour, expected):
    now = datetime(2026, 5, 29, hour, 0, 0)
    assert format_time(now) == expected


# --- get_condition_and_emoji --------------------------------------------------
def test_condition_lightning_first():
    obs = Observation(lightning_strike_count=2, solar_radiation=900)
    cond, emoji = get_condition_and_emoji(obs)
    assert cond == "Tstorm"


def test_condition_active_rain():
    obs = Observation(rain_last_minute_in=0.05, solar_radiation=200)
    cond, _ = get_condition_and_emoji(obs)
    assert cond == "Rain"


def test_condition_night_from_low_illuminance():
    obs = Observation(illuminance=10)
    cond, _ = get_condition_and_emoji(obs)
    assert cond == "Clear"  # night = "Clear" + moon emoji


def test_condition_sunny():
    obs = Observation(illuminance=80000, solar_radiation=900)
    cond, _ = get_condition_and_emoji(obs)
    assert cond == "Sunny"


def test_condition_partly_cloudy():
    obs = Observation(solar_radiation=500)
    cond, _ = get_condition_and_emoji(obs)
    assert cond == "Partly"


def test_condition_cloudy():
    obs = Observation(solar_radiation=100)
    cond, _ = get_condition_and_emoji(obs)
    assert cond == "Cloudy"


def test_condition_fallback_no_signals():
    obs = Observation()
    cond, _ = get_condition_and_emoji(obs)
    assert cond == "Sunny"


# --- format_conditions_message ------------------------------------------------
def test_format_conditions_imperial():
    cfg = MessageConfig(units="imperial")
    obs = Observation(
        temperature_f=53,
        humidity=93,
        wind_avg_mph=6,
        wind_direction=90,
        uv_index=0,
        solar_radiation=900,
    )
    msg = format_conditions_message(
        obs,
        location="Glendale",
        config=cfg,
        now=datetime(2026, 5, 29, 9, 0, 0),
    )
    # Verify all the parts
    assert "WX Glendale 9AM" in msg
    assert "53°F" in msg
    assert "Sunny" in msg
    assert "Wind 6 mph E" in msg
    assert "RH 93%" in msg
    assert "UV 0" in msg


def test_format_conditions_metric():
    cfg = MessageConfig(units="metric")
    obs = Observation(
        temperature_c=12,
        humidity=80,
        wind_avg_kmh=10,
        wind_direction=180,
    )
    msg = format_conditions_message(
        obs,
        location="Berlin",
        config=cfg,
        now=datetime(2026, 5, 29, 14, 0, 0),
    )
    assert "12°C" in msg
    assert "km/h" in msg
    assert " S" in msg  # south wind


def test_format_conditions_truncates_long_location():
    cfg = MessageConfig(units="imperial", location_label_max_chars=8)
    obs = Observation(temperature_f=70, humidity=50, wind_avg_mph=0)
    msg = format_conditions_message(
        obs, location="ReallyLongTown", config=cfg, now=datetime(2026, 5, 29, 12, 0, 0)
    )
    assert "ReallyLo" in msg
    assert "ReallyLongTown" not in msg


def test_format_conditions_handles_missing_temp():
    cfg = MessageConfig(units="imperial")
    obs = Observation(humidity=50)
    msg = format_conditions_message(obs, "X", cfg, datetime(2026, 5, 29, 12, 0, 0))
    assert "N/A" in msg


# --- truncation --------------------------------------------------------------
def test_enforce_max_bytes_keeps_short_string():
    assert _enforce_max_bytes("hello", 200) == "hello"


def test_enforce_max_bytes_truncates_ascii():
    out = _enforce_max_bytes("a" * 300, 100)
    assert len(out.encode("utf-8")) <= 100


def test_enforce_max_bytes_emoji_safe():
    """Truncation must not produce a half-emoji that breaks UTF-8 decode."""
    s = "x" * 195 + "☀️"  # ☀️ is 6 bytes in UTF-8
    out = _enforce_max_bytes(s, 200)
    # Round-trip cleanly
    out.encode("utf-8").decode("utf-8")


# --- severe templates --------------------------------------------------------
def test_current_rain_message_fresh_start():
    msg = format_current_rain_message("Camp X", 0.5, 2.0, is_fresh_start=True)
    assert "Heads up" in msg
    assert "Camp X" in msg
    assert "0.5mm/hr" in msg


def test_current_rain_message_sustained():
    msg = format_current_rain_message("Camp X", 0.5, 2.0, is_fresh_start=False)
    assert "Still raining" in msg


def test_current_rain_message_heavy_prefix():
    msg = format_current_rain_message("Camp X", 3.0, 2.0, is_fresh_start=True)
    assert "Heavy rain" in msg


def test_forecast_lead_message_normal():
    msg = format_forecast_lead_message(
        "Camp Y", hours_ahead=4, max_precip_mm=0.5, heavy_threshold_mm_per_hr=2.0
    )
    assert "Camp Y" in msg
    assert "in ~4h" in msg
    assert "secure" in msg.lower()


def test_forecast_lead_message_heavy():
    msg = format_forecast_lead_message(
        "Camp Y", hours_ahead=2, max_precip_mm=5.0, heavy_threshold_mm_per_hr=2.0
    )
    assert "Heavy rain" in msg


def test_nws_alert_message_with_headline():
    msg = format_nws_alert_message(
        event="Severe Thunderstorm Warning",
        expires_iso="2026-05-29T18:30:00+00:00",
        headline="Severe Thunderstorm Warning... damaging winds, large hail",
    )
    assert "⚠️" in msg
    assert "Severe Thunderstorm Warning" in msg


def test_nws_alert_message_no_headline():
    msg = format_nws_alert_message("Flash Flood Warning", None, None)
    assert "⚠️" in msg
    assert "Flash Flood Warning" in msg


def test_open_meteo_transition_message():
    msg = format_open_meteo_transition_message("Camp", ["🌧️ Rain", "💨 Wind 25+ mph"], "3p")
    assert msg is not None
    assert "Camp" in msg
    assert "🌧️ Rain" in msg
    assert "3p" in msg


def test_open_meteo_transition_message_empty():
    assert format_open_meteo_transition_message("Camp", [], "3p") is None
