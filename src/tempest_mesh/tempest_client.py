"""Tempest WeatherFlow client.

Supports three sources:

- ``cloud`` — REST: ``GET https://swd.weatherflow.com/swd/rest/observations/station/<id>?token=<t>``
- ``udp`` — local UDP broadcast on port 50222 from the Tempest Hub on the LAN
- ``garden_api`` — a separate HTTP listener you host (advanced, see docs)

All three return a normalized observation dict with these keys (any may be None):

- temperature_f, temperature_c
- humidity (0-100)
- wind_avg_mph, wind_avg_kmh, wind_direction (deg), wind_gust_mph
- pressure_inhg, pressure_hpa
- uv_index, illuminance, solar_radiation
- daily_rain_in, daily_rain_mm
- rain_last_minute_in, rain_last_minute_mm
- lightning_strike_count, lightning_strike_avg_distance_km
- raw_obs (the raw payload, for debugging)
"""

from __future__ import annotations

import json
import logging
import socket
from dataclasses import dataclass, field
from typing import Any

import requests

from .config import AppConfig

logger = logging.getLogger(__name__)


CLOUD_BASE_URL = "https://swd.weatherflow.com/swd/rest/observations/station"


# Tempest "obs_st" UDP packet field order (per WeatherFlow Tempest API docs).
# https://weatherflow.github.io/Tempest/api/udp/v143/
_UDP_OBS_ST_FIELDS = [
    "epoch",
    "wind_lull_ms",
    "wind_avg_ms",
    "wind_gust_ms",
    "wind_direction",
    "wind_sample_interval",
    "pressure_mb",
    "air_temperature_c",
    "relative_humidity",
    "illuminance",
    "uv_index",
    "solar_radiation",
    "rain_accumulated_mm",
    "precipitation_type",
    "lightning_strike_avg_distance_km",
    "lightning_strike_count",
    "battery_v",
    "report_interval_min",
]


class TempestFetchError(Exception):
    """Raised when we couldn't fetch a usable observation from any source."""


@dataclass
class Observation:
    """Normalized, unit-agnostic observation. Imperial + metric both present."""

    temperature_f: float | None = None
    temperature_c: float | None = None
    humidity: float | None = None
    wind_avg_mph: float | None = None
    wind_avg_kmh: float | None = None
    wind_direction: float | None = None
    wind_gust_mph: float | None = None
    wind_gust_kmh: float | None = None
    pressure_inhg: float | None = None
    pressure_hpa: float | None = None
    uv_index: float | None = None
    illuminance: float | None = None
    solar_radiation: float | None = None
    daily_rain_in: float | None = None
    daily_rain_mm: float | None = None
    rain_last_minute_in: float | None = None
    rain_last_minute_mm: float | None = None
    lightning_strike_count: int | None = None
    lightning_strike_avg_distance_km: float | None = None
    raw_obs: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "temperature_f": self.temperature_f,
            "temperature_c": self.temperature_c,
            "humidity": self.humidity,
            "wind_avg_mph": self.wind_avg_mph,
            "wind_avg_kmh": self.wind_avg_kmh,
            "wind_direction": self.wind_direction,
            "wind_gust_mph": self.wind_gust_mph,
            "wind_gust_kmh": self.wind_gust_kmh,
            "pressure_inhg": self.pressure_inhg,
            "pressure_hpa": self.pressure_hpa,
            "uv_index": self.uv_index,
            "illuminance": self.illuminance,
            "solar_radiation": self.solar_radiation,
            "daily_rain_in": self.daily_rain_in,
            "daily_rain_mm": self.daily_rain_mm,
            "rain_last_minute_in": self.rain_last_minute_in,
            "rain_last_minute_mm": self.rain_last_minute_mm,
            "lightning_strike_count": self.lightning_strike_count,
            "lightning_strike_avg_distance_km": self.lightning_strike_avg_distance_km,
        }


def _c_to_f(c: float | None) -> float | None:
    return None if c is None else c * 9 / 5 + 32


def _ms_to_mph(ms: float | None) -> float | None:
    return None if ms is None else ms * 2.23694


def _ms_to_kmh(ms: float | None) -> float | None:
    return None if ms is None else ms * 3.6


def _mb_to_inhg(mb: float | None) -> float | None:
    return None if mb is None else mb * 0.02953


def _mm_to_in(mm: float | None) -> float | None:
    return None if mm is None else mm / 25.4


def _obs_from_cloud_payload(payload: dict) -> Observation:
    """Parse a WeatherFlow REST ``GET /observations/station/<id>`` payload.

    The REST shape returns ``obs`` as a list of dicts with imperial-ish keys
    when ``units_*`` query params are unset. We treat the first entry as current.
    """
    obs_list = payload.get("obs") or []
    if not obs_list:
        raise TempestFetchError("WeatherFlow cloud returned no observations")
    cur = obs_list[0] or {}

    temp_c = cur.get("air_temperature")
    humidity = cur.get("relative_humidity")
    wind_avg_ms = cur.get("wind_avg")
    wind_gust_ms = cur.get("wind_gust")
    wind_dir = cur.get("wind_direction")
    pressure_mb = cur.get("station_pressure") or cur.get("sea_level_pressure")
    uv = cur.get("uv")
    illuminance = cur.get("brightness")
    solar = cur.get("solar_radiation")
    rain_today_mm = (
        cur.get("precip_accum_local_day") or cur.get("precip_accum_local_yesterday") or 0
    )
    rain_minute_mm = cur.get("precip_minute_local") or cur.get("precip") or 0
    lightning_count = cur.get("lightning_strike_count_last_3hr") or 0
    lightning_dist = cur.get("lightning_strike_last_distance")

    return Observation(
        temperature_c=temp_c,
        temperature_f=_c_to_f(temp_c),
        humidity=humidity,
        wind_avg_mph=_ms_to_mph(wind_avg_ms),
        wind_avg_kmh=_ms_to_kmh(wind_avg_ms),
        wind_gust_mph=_ms_to_mph(wind_gust_ms),
        wind_gust_kmh=_ms_to_kmh(wind_gust_ms),
        wind_direction=wind_dir,
        pressure_hpa=pressure_mb,
        pressure_inhg=_mb_to_inhg(pressure_mb),
        uv_index=uv,
        illuminance=illuminance,
        solar_radiation=solar,
        daily_rain_mm=rain_today_mm,
        daily_rain_in=_mm_to_in(rain_today_mm),
        rain_last_minute_mm=rain_minute_mm,
        rain_last_minute_in=_mm_to_in(rain_minute_mm),
        lightning_strike_count=lightning_count,
        lightning_strike_avg_distance_km=lightning_dist,
        raw_obs=cur,
    )


def _obs_from_udp_packet(packet: dict) -> Observation:
    """Parse a Tempest UDP ``obs_st`` packet.

    Packets look like ``{"type":"obs_st","obs":[[ ... 18 values ... ]], ...}``.
    """
    if packet.get("type") != "obs_st":
        raise TempestFetchError(f"unexpected UDP packet type {packet.get('type')!r}")
    obs_arrays = packet.get("obs") or []
    if not obs_arrays:
        raise TempestFetchError("UDP obs_st packet had no obs array")
    arr = obs_arrays[0] if isinstance(obs_arrays[0], list) else obs_arrays
    raw: dict[str, Any] = {}
    for i, name in enumerate(_UDP_OBS_ST_FIELDS):
        if i < len(arr):
            raw[name] = arr[i]

    temp_c = raw.get("air_temperature_c")
    wind_avg_ms = raw.get("wind_avg_ms")
    wind_gust_ms = raw.get("wind_gust_ms")
    pressure_mb = raw.get("pressure_mb")
    rain_min_mm = raw.get("rain_accumulated_mm")  # per report interval

    return Observation(
        temperature_c=temp_c,
        temperature_f=_c_to_f(temp_c),
        humidity=raw.get("relative_humidity"),
        wind_avg_mph=_ms_to_mph(wind_avg_ms),
        wind_avg_kmh=_ms_to_kmh(wind_avg_ms),
        wind_gust_mph=_ms_to_mph(wind_gust_ms),
        wind_gust_kmh=_ms_to_kmh(wind_gust_ms),
        wind_direction=raw.get("wind_direction"),
        pressure_hpa=pressure_mb,
        pressure_inhg=_mb_to_inhg(pressure_mb),
        uv_index=raw.get("uv_index"),
        illuminance=raw.get("illuminance"),
        solar_radiation=raw.get("solar_radiation"),
        rain_last_minute_mm=rain_min_mm,
        rain_last_minute_in=_mm_to_in(rain_min_mm),
        lightning_strike_count=raw.get("lightning_strike_count") or 0,
        lightning_strike_avg_distance_km=raw.get("lightning_strike_avg_distance_km"),
        raw_obs=raw,
    )


def _obs_from_garden_api(payload: dict) -> Observation:
    """Parse a /api/sensors/tempest-local style payload (already imperial)."""
    obs = payload.get("observation") or payload
    temp_f = obs.get("temperature_f")
    return Observation(
        temperature_f=temp_f,
        temperature_c=(None if temp_f is None else (temp_f - 32) * 5 / 9),
        humidity=obs.get("humidity"),
        wind_avg_mph=obs.get("wind_avg_mph"),
        wind_gust_mph=obs.get("wind_gust_mph"),
        wind_direction=obs.get("wind_direction"),
        pressure_inhg=obs.get("pressure_inhg"),
        uv_index=obs.get("uv_index"),
        illuminance=obs.get("illuminance"),
        solar_radiation=obs.get("solar_radiation"),
        daily_rain_in=obs.get("daily_rain_in"),
        rain_last_minute_in=obs.get("rain_last_minute_in"),
        raw_obs=obs,
    )


class TempestClient:
    """Polymorphic Tempest fetcher (cloud | udp | garden_api)."""

    def __init__(self, config: AppConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()

    def fetch(self) -> Observation:
        src = self.config.tempest.source
        if src == "cloud":
            return self._fetch_cloud()
        if src == "udp":
            return self._fetch_udp()
        if src == "garden_api":
            return self._fetch_garden_api()
        raise TempestFetchError(f"unknown tempest source {src!r}")

    # --- cloud ---------------------------------------------------------------
    def _fetch_cloud(self) -> Observation:
        token = self.config.tempest.api_token
        station = self.config.tempest.station_id
        if not token or not station:
            raise TempestFetchError(
                "TEMPEST_API_TOKEN + TEMPEST_STATION_ID are required for source=cloud"
            )
        url = f"{CLOUD_BASE_URL}/{station}"
        resp = self.session.get(url, params={"token": token}, timeout=15)
        if resp.status_code == 401:
            raise TempestFetchError("WeatherFlow returned 401 — check TEMPEST_API_TOKEN")
        if resp.status_code == 404:
            raise TempestFetchError(
                f"WeatherFlow returned 404 — check TEMPEST_STATION_ID={station!r}"
            )
        resp.raise_for_status()
        return _obs_from_cloud_payload(resp.json())

    # --- udp -----------------------------------------------------------------
    def _fetch_udp(self) -> Observation:
        port = self.config.tempest.udp_port
        listen_for = self.config.tempest.udp_listen_seconds
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", port))
        except OSError as e:
            raise TempestFetchError(
                f"could not bind UDP :{port} for Tempest broadcasts ({e}). "
                "Another process may own the socket; consider TEMPEST_SOURCE=garden_api."
            ) from e
        sock.settimeout(listen_for)
        try:
            while True:
                data, _ = sock.recvfrom(2048)
                try:
                    pkt = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if pkt.get("type") == "obs_st":
                    return _obs_from_udp_packet(pkt)
        except TimeoutError as e:
            raise TempestFetchError(
                f"no obs_st packet on UDP :{port} within {listen_for}s — "
                "is the Tempest Hub powered on and on this LAN?"
            ) from e
        finally:
            sock.close()

    # --- garden_api ----------------------------------------------------------
    def _fetch_garden_api(self) -> Observation:
        url = self.config.tempest.garden_api_url
        token = self.config.tempest.local_token
        if not url:
            raise TempestFetchError("GARDEN_API_URL is required for source=garden_api")
        headers = {"X-Tempest-Token": token} if token else {}
        resp = self.session.get(
            f"{url.rstrip('/')}/api/sensors/tempest-local",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return _obs_from_garden_api(resp.json())
