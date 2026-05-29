"""Format Tempest observations into short Meshtastic-friendly broadcast strings.

Meshtastic LoRa channels have a small payload budget. We default to imperial
units (configurable to metric) and stay under 200 bytes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .config import MessageConfig
from .tempest_client import Observation

_COMPASS_POINTS = [
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
]


def deg_to_compass(degrees: float | None) -> str:
    """Convert a wind direction in degrees (0-360) to a 16-pt compass label."""
    if degrees is None:
        return ""
    try:
        d = float(degrees)
    except (TypeError, ValueError):
        return ""
    idx = int((d + 11.25) / 22.5) % 16
    return _COMPASS_POINTS[idx]


def format_time(now: datetime | None = None) -> str:
    """Format the current local time as a short ``9AM`` / ``11PM`` label."""
    now = now or datetime.now()
    period = "AM" if now.hour < 12 else "PM"
    hour_12 = 12 if now.hour in (0, 12) else (now.hour if now.hour < 12 else now.hour - 12)
    return f"{hour_12}{period}"


def get_condition_and_emoji(obs: Observation) -> tuple[str, str]:
    """Derive a short condition label + emoji from raw Tempest signals.

    Order: lightning > active precip > day/night (illuminance) > cloud cover (solar).
    """
    if obs.lightning_strike_count and obs.lightning_strike_count > 0:
        return ("Tstorm", "⛈️")  # ⛈️

    rain_minute = obs.rain_last_minute_in
    if isinstance(rain_minute, (int, float)) and rain_minute > 0:
        return ("Rain", "\U0001f327️")  # 🌧️

    is_night = False
    if isinstance(obs.illuminance, (int, float)):
        is_night = obs.illuminance < 100
    elif isinstance(obs.solar_radiation, (int, float)):
        is_night = obs.solar_radiation < 10

    if is_night:
        return ("Clear", "\U0001f319")  # 🌙

    if isinstance(obs.solar_radiation, (int, float)):
        if obs.solar_radiation < 300:
            return ("Cloudy", "☁️")  # ☁️
        if obs.solar_radiation < 800:
            return ("Partly", "⛅")  # ⛅
        return ("Sunny", "☀️")  # ☀️

    if isinstance(obs.illuminance, (int, float)):
        if obs.illuminance < 1000:
            return ("Cloudy", "☁️")
        if obs.illuminance < 5000:
            return ("Partly", "⛅")
        return ("Sunny", "☀️")

    return ("Sunny", "☀️")


def _truncate_label(name: str, max_chars: int) -> str:
    name = (name or "").strip() or "Unknown"
    return name[:max_chars]


def format_conditions_message(
    obs: Observation,
    location: str,
    config: MessageConfig,
    now: datetime | None = None,
) -> str:
    """Render the conditions broadcast (one line, ≤200 bytes by default).

    Imperial example:
        ``WX Glendale 9AM: 53°F Sunny ☀️ | Wind 6 mph E | RH 93% | UV 0``

    Metric example:
        ``WX Glendale 9AM: 12°C Sunny ☀️ | Wind 10 km/h E | RH 93% | UV 0``
    """
    location_label = _truncate_label(location, config.location_label_max_chars)
    time_str = format_time(now)

    if config.units == "metric":
        temp = obs.temperature_c
        temp_str = f"{int(round(temp))}°C" if temp is not None else "N/A"
        wind = obs.wind_avg_kmh
        wind_str = f"{int(round(wind))} km/h" if wind is not None else "N/A"
    else:
        temp = obs.temperature_f
        temp_str = f"{int(round(temp))}°F" if temp is not None else "N/A"
        wind = obs.wind_avg_mph
        wind_str = f"{int(round(wind))} mph" if wind is not None else "N/A"

    cond, emoji = get_condition_and_emoji(obs)

    wind_dir = deg_to_compass(obs.wind_direction)
    if wind_dir:
        wind_str = f"{wind_str} {wind_dir}"

    humidity_str = f"{int(obs.humidity)}%" if isinstance(obs.humidity, (int, float)) else "N/A"

    parts = [
        f"WX {location_label} {time_str}: {temp_str} {cond} {emoji}",
        f"Wind {wind_str}",
        f"RH {humidity_str}",
    ]
    if obs.uv_index is not None:
        parts.append(f"UV {int(round(obs.uv_index))}")

    message = " | ".join(parts)
    return _enforce_max_bytes(message, config.message_max_chars)


def _enforce_max_bytes(message: str, max_chars: int) -> str:
    """Truncate the message to ``max_chars`` BYTES (UTF-8). Emoji-safe.

    We slice the UTF-8 bytes and decode with errors='ignore' so we never
    return a half-emoji that would make the receiver puke.
    """
    encoded = message.encode("utf-8")
    if len(encoded) <= max_chars:
        return message
    return encoded[:max_chars].decode("utf-8", errors="ignore")


# --- severe-weather messaging --------------------------------------------------


def format_current_rain_message(
    location: str,
    precip_mm_per_hr: float,
    heavy_threshold_mm_per_hr: float,
    is_fresh_start: bool,
    max_chars: int = 200,
) -> str:
    """Render the "it's raining right now" alert (Source #3)."""
    if is_fresh_start:
        base = f"Heads up — rain at {location}. {precip_mm_per_hr:.1f}mm/hr ☔"
    else:
        base = f"Still raining at {location}. {precip_mm_per_hr:.1f}mm/hr"
    if precip_mm_per_hr >= heavy_threshold_mm_per_hr:
        base = "⚠️ Heavy rain — " + base
    return _enforce_max_bytes(base, max_chars)


def format_forecast_lead_message(
    location: str,
    hours_ahead: int,
    max_precip_mm: float,
    heavy_threshold_mm_per_hr: float,
    max_chars: int = 200,
) -> str:
    """Render the "rain in N hours, secure camp" forecast-lead alert (Source #4)."""
    if max_precip_mm >= heavy_threshold_mm_per_hr:
        msg = f"⚠️ Heavy rain expected at {location} in ~{hours_ahead}h. Secure your camp. ⛺☔"
    else:
        msg = (
            f"Rain expected at {location} in ~{hours_ahead}h. "
            "Get back to camp if you have stuff to secure. ⛺☔"
        )
    return _enforce_max_bytes(msg, max_chars)


def format_nws_alert_message(
    event: str,
    expires_iso: str | None,
    headline: str | None,
    max_chars: int = 200,
) -> str:
    """Render an NWS active-alert broadcast (Source #1)."""
    short = event
    if expires_iso:
        try:
            dt = datetime.fromisoformat(expires_iso.replace("Z", "+00:00"))
            short = f"{event} until {dt.strftime('%-I:%M%p').lower()}"
        except ValueError:
            pass
    if headline:
        snippet = headline.split("...")[-1].strip()[:100]
        msg = f"⚠️ {short} – {snippet}"
    else:
        msg = f"⚠️ {short}"
    return _enforce_max_bytes(msg, max_chars)


def format_open_meteo_transition_message(
    location: str,
    alerts: list[str],
    next_hour_label: str,
    max_chars: int = 200,
) -> str | None:
    """Render the Open-Meteo "starting in next hour" backstop (Source #2)."""
    if not alerts:
        return None
    msg = f"⚠️ {location} – In ~1 hr ({next_hour_label}): " + " | ".join(alerts)
    return _enforce_max_bytes(msg, max_chars)


Units = Literal["imperial", "metric"]
