"""Configuration schema + loader for tempest-meshtastic-weather.

Loads from a YAML file, then overlays environment variables. Pydantic validates
the shape and surfaces actionable error messages on missing / wrong-typed fields.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(value: object) -> object:
    """Recursively replace ${VAR} occurrences in strings with os.environ values."""
    if isinstance(value, str):

        def repl(match: re.Match[str]) -> str:
            return os.environ.get(match.group(1), "")

        return _ENV_VAR_PATTERN.sub(repl, value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


class TempestConfig(BaseModel):
    source: Literal["cloud", "udp", "garden_api"] = "cloud"
    api_token: str = ""
    station_id: str = ""
    udp_port: int = 50222
    udp_listen_seconds: int = 60
    garden_api_url: str = ""
    local_token: str = ""

    @field_validator("station_id", mode="before")
    @classmethod
    def coerce_station_id(cls, v: object) -> str:
        # WeatherFlow station IDs are integers; accept either.
        if v is None:
            return ""
        return str(v)


class MeshMonitorConfig(BaseModel):
    url: str = "http://localhost:8080"
    api_token: str = ""
    channel_index: int = 0
    retry_max_attempts: int = 3
    retry_sleep_seconds: int = 30
    retry_timeout_seconds: int = 15


class LocationConfig(BaseModel):
    strategy: Literal["fixed", "from_meshmonitor"] = "fixed"
    name: str = "Home"
    lat: float | None = None
    lon: float | None = None
    meshmonitor_node_id: str = ""
    position_max_age_seconds: int = 86400


class MessageConfig(BaseModel):
    units: Literal["imperial", "metric"] = "imperial"
    location_label_max_chars: int = 15
    message_max_chars: int = 200


class SevereAlerterConfig(BaseModel):
    enabled: bool = True
    nws_user_agent: str = "tempest-meshtastic-weather (set-me@example.com)"
    channel_index: int = 0
    dedup_ttl_hours: int = 6
    forecast_lead_hours: int = 6
    forecast_lead_debounce_seconds: int = 3600
    heavy_rain_mm_per_hr: float = 2.0
    min_rain_mm: float = 0.1
    current_rain_debounce_seconds: int = 1800
    state_dir: str = "./state"


class LoggingConfig(BaseModel):
    level: str = "INFO"


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tempest: TempestConfig = Field(default_factory=TempestConfig)
    meshmonitor: MeshMonitorConfig = Field(default_factory=MeshMonitorConfig)
    location: LocationConfig = Field(default_factory=LocationConfig)
    message: MessageConfig = Field(default_factory=MessageConfig)
    severe_alerter: SevereAlerterConfig = Field(default_factory=SevereAlerterConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def _apply_env_overrides(cfg: AppConfig) -> AppConfig:
    """Environment variables override YAML so ops can rotate tokens without editing files."""
    env = os.environ

    # Tempest
    if "TEMPEST_SOURCE" in env:
        cfg.tempest.source = env["TEMPEST_SOURCE"]  # type: ignore[assignment]
    if "TEMPEST_API_TOKEN" in env:
        cfg.tempest.api_token = env["TEMPEST_API_TOKEN"]
    if "TEMPEST_STATION_ID" in env:
        cfg.tempest.station_id = env["TEMPEST_STATION_ID"]
    if "TEMPEST_UDP_PORT" in env:
        cfg.tempest.udp_port = int(env["TEMPEST_UDP_PORT"])
    if "TEMPEST_UDP_LISTEN_SECONDS" in env:
        cfg.tempest.udp_listen_seconds = int(env["TEMPEST_UDP_LISTEN_SECONDS"])
    if "GARDEN_API_URL" in env:
        cfg.tempest.garden_api_url = env["GARDEN_API_URL"]
    if "TEMPEST_LOCAL_TOKEN" in env:
        cfg.tempest.local_token = env["TEMPEST_LOCAL_TOKEN"]

    # MeshMonitor
    if "MESHMONITOR_URL" in env:
        cfg.meshmonitor.url = env["MESHMONITOR_URL"]
    if "MESHMONITOR_API_TOKEN" in env:
        cfg.meshmonitor.api_token = env["MESHMONITOR_API_TOKEN"]
    if "MESHMONITOR_CHANNEL_INDEX" in env:
        cfg.meshmonitor.channel_index = int(env["MESHMONITOR_CHANNEL_INDEX"])

    # Location
    if "LOCATION_STRATEGY" in env:
        cfg.location.strategy = env["LOCATION_STRATEGY"]  # type: ignore[assignment]
    if "LOCATION_NAME" in env:
        cfg.location.name = env["LOCATION_NAME"]
    if "LOCATION_LAT" in env and env["LOCATION_LAT"]:
        cfg.location.lat = float(env["LOCATION_LAT"])
    if "LOCATION_LON" in env and env["LOCATION_LON"]:
        cfg.location.lon = float(env["LOCATION_LON"])
    if "LOCATION_MESHMONITOR_NODE_ID" in env:
        cfg.location.meshmonitor_node_id = env["LOCATION_MESHMONITOR_NODE_ID"]
    if "LOCATION_POSITION_MAX_AGE_SECONDS" in env:
        cfg.location.position_max_age_seconds = int(env["LOCATION_POSITION_MAX_AGE_SECONDS"])

    # Severe alerter
    if "SEVERE_ALERTER_ENABLED" in env:
        cfg.severe_alerter.enabled = env["SEVERE_ALERTER_ENABLED"].lower() in ("1", "true", "yes")
    if "SEVERE_NWS_USER_AGENT" in env:
        cfg.severe_alerter.nws_user_agent = env["SEVERE_NWS_USER_AGENT"]
    if "SEVERE_ALERT_CHANNEL_INDEX" in env:
        cfg.severe_alerter.channel_index = int(env["SEVERE_ALERT_CHANNEL_INDEX"])
    if "SEVERE_DEDUP_TTL_HOURS" in env:
        cfg.severe_alerter.dedup_ttl_hours = int(env["SEVERE_DEDUP_TTL_HOURS"])
    if "SEVERE_FORECAST_LEAD_HOURS" in env:
        cfg.severe_alerter.forecast_lead_hours = int(env["SEVERE_FORECAST_LEAD_HOURS"])
    if "SEVERE_FORECAST_LEAD_DEBOUNCE_SECONDS" in env:
        cfg.severe_alerter.forecast_lead_debounce_seconds = int(
            env["SEVERE_FORECAST_LEAD_DEBOUNCE_SECONDS"]
        )
    if "SEVERE_CURRENT_RAIN_DEBOUNCE_SECONDS" in env:
        cfg.severe_alerter.current_rain_debounce_seconds = int(
            env["SEVERE_CURRENT_RAIN_DEBOUNCE_SECONDS"]
        )
    if "SEVERE_HEAVY_RAIN_MM_PER_HR" in env:
        cfg.severe_alerter.heavy_rain_mm_per_hr = float(env["SEVERE_HEAVY_RAIN_MM_PER_HR"])
    if "SEVERE_MIN_RAIN_MM" in env:
        cfg.severe_alerter.min_rain_mm = float(env["SEVERE_MIN_RAIN_MM"])
    if "SEVERE_STATE_DIR" in env:
        cfg.severe_alerter.state_dir = env["SEVERE_STATE_DIR"]

    if "TEMPEST_MESH_LOG_LEVEL" in env:
        cfg.logging.level = env["TEMPEST_MESH_LOG_LEVEL"]

    return cfg


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load + validate config from yaml + env overlay.

    If ``path`` is None and ``config.yaml`` does not exist, returns defaults +
    env overlay only — useful for "env-only" deployments (Docker, Heroku-style).
    """
    raw: dict = {}
    if path is None:
        for candidate in ("config.yaml", "config.yml"):
            if Path(candidate).is_file():
                path = candidate
                break
    if path is not None and Path(path).is_file():
        with open(path) as f:
            loaded = yaml.safe_load(f) or {}
        raw = _expand_env(loaded)  # type: ignore[assignment]

    cfg = AppConfig(**raw)
    return _apply_env_overrides(cfg)


def require(cond: bool, message: str) -> None:
    """Tiny config-validation helper used by runners."""
    if not cond:
        raise ValueError(f"Configuration error: {message}")
