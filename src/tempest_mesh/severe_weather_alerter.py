"""Severe-weather alerter.

Four broadcast sources, evaluated in priority order:

  1. NWS active alerts (severe / extreme) at the broadcast point
  2. Open-Meteo "starting in next hour" transition deltas
  3. Open-Meteo "currently raining" (debounced)
  4. Open-Meteo "rain in next 4-6 hours" forecast-lead heads-up

Sources cascade: NWS suppresses #4. A recent #3 broadcast suppresses #4.
NWS rain alerts suppress #3 (already covered).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from .config import AppConfig
from .meshmonitor_client import MeshMonitorClient
from .message_formatter import (
    format_current_rain_message,
    format_forecast_lead_message,
    format_nws_alert_message,
    format_open_meteo_transition_message,
)

logger = logging.getLogger(__name__)


NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass
class AlertRunResult:
    sent: list[str] = field(default_factory=list)
    suppressed: list[str] = field(default_factory=list)
    skipped_reason: str | None = None


def _state_path(state_dir: str, name: str) -> Path:
    p = Path(state_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p / name


def _read_timestamp(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(path.read_text().strip() or 0)
    except (ValueError, OSError):
        return 0


def _write_timestamp(path: Path, ts: int) -> None:
    try:
        path.write_text(str(ts))
    except OSError as e:
        logger.warning("failed writing timestamp to %s: %s", path, e)


def _load_dedup(path: Path, ttl_seconds: int) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    seen = data.get("seen", {}) if isinstance(data, dict) else {}
    now = int(time.time())
    return {k: int(v) for k, v in seen.items() if (now - int(v)) < ttl_seconds}


def _save_dedup(path: Path, seen: dict[str, int]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"seen": seen, "updated": int(time.time())}))
    except OSError as e:
        logger.warning("dedup write failed for %s: %s", path, e)


# ---------------------------------------------------------------------------
# Source #1 — NWS
# ---------------------------------------------------------------------------
def fetch_nws_alerts(
    lat: float,
    lon: float,
    user_agent: str,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """Return active NWS alerts at the broadcast point.

    NWS requires a contact-bearing User-Agent. Returns ``[]`` on any failure.
    See https://www.weather.gov/documentation/services-web-api
    """
    s = session or requests.Session()
    try:
        resp = s.get(
            NWS_ALERTS_URL,
            params={"point": f"{lat},{lon}"},
            headers={"User-Agent": user_agent, "Accept": "application/geo+json"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("NWS returned %s: %s", resp.status_code, resp.text[:200])
            return []
        body = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("NWS fetch failed: %s", e)
        return []

    out: list[dict[str, Any]] = []
    for feat in body.get("features", []) or []:
        props = feat.get("properties") or {}
        sev = (props.get("severity") or "").lower()
        if sev not in ("severe", "extreme"):
            continue
        aid = props.get("id") or feat.get("id")
        if not aid:
            continue
        out.append(
            {
                "id": aid,
                "event": props.get("event") or "Alert",
                "headline": (props.get("headline") or "").strip(),
                "expires": props.get("expires"),
                "severity": sev,
            }
        )
    return out


def dedup_alerts(
    alerts: list[dict[str, Any]],
    dedup_path: Path,
    ttl_hours: int,
) -> list[dict[str, Any]]:
    """Drop alerts we've broadcast within ``ttl_hours``; persist new ones."""
    seen = _load_dedup(dedup_path, ttl_hours * 3600)
    now = int(time.time())
    fresh: list[dict[str, Any]] = []
    for a in alerts:
        if a["id"] in seen:
            continue
        fresh.append(a)
        seen[a["id"]] = now
    if fresh:
        _save_dedup(dedup_path, seen)
    return fresh


# ---------------------------------------------------------------------------
# Source #2 — Open-Meteo "starting in next hour"
# ---------------------------------------------------------------------------
def _fetch_open_meteo(
    params: dict[str, Any], session: requests.Session | None = None
) -> dict | None:
    s = session or requests.Session()
    try:
        resp = s.get(OPEN_METEO_URL, params=params, timeout=15)
        if resp.status_code != 200:
            logger.warning("Open-Meteo returned %s: %s", resp.status_code, resp.text[:200])
            return None
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Open-Meteo fetch failed: %s", e)
        return None


def detect_next_hour_transitions(
    forecast: dict,
    now: datetime | None = None,
) -> tuple[list[str], str | None]:
    """Find conditions that START in the next hour but aren't present now."""
    now = now or datetime.now()
    h = forecast.get("hourly", {}) or {}
    times: list[str] = h.get("time", []) or []
    codes: list[Any] = h.get("weather_code", []) or []
    wind_speed: list[Any] = h.get("wind_speed_10m", []) or []
    wind_gusts: list[Any] = h.get("wind_gusts_10m", []) or []
    precip: list[Any] = h.get("precipitation", []) or []

    nxt = now + timedelta(hours=1)

    cur_idx: int | None = None
    nxt_idx: int | None = None
    next_label: str | None = None
    for i, t in enumerate(times):
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.date() == now.date() and dt.hour == now.hour:
            cur_idx = i
        if dt.date() == nxt.date() and dt.hour == nxt.hour:
            nxt_idx = i
            next_label = _hour_to_label(dt.hour)

    if nxt_idx is None:
        return ([], next_label)

    def gv(arr: list[Any], i: int | None) -> Any:
        return arr[i] if arr and i is not None and i < len(arr) else None

    def is_wind(g: Any, w: Any) -> bool:
        return (g is not None and g >= 25) or (w is not None and w >= 20)

    def is_rain(p: Any) -> bool:
        return (p or 0) > 0.01

    def is_tstorm(c: Any) -> bool:
        try:
            return int(c) in (95, 96, 99)
        except (TypeError, ValueError):
            return False

    def is_other(c: Any) -> bool:
        try:
            return int(c) in (66, 67, 75, 48)
        except (TypeError, ValueError):
            return False

    cg, cw, cp = gv(wind_gusts, cur_idx), gv(wind_speed, cur_idx), gv(precip, cur_idx) or 0
    cc = gv(codes, cur_idx) or 0
    ng, nw, np_ = gv(wind_gusts, nxt_idx), gv(wind_speed, nxt_idx), gv(precip, nxt_idx) or 0
    nc = gv(codes, nxt_idx) or 0

    alerts: list[str] = []
    if is_wind(ng, nw) and not is_wind(cg, cw):
        alerts.append("💨 Wind 25+ mph")
    if is_rain(np_) and not is_rain(cp):
        alerts.append("🌧️ Rain")
    if is_tstorm(nc) and not is_tstorm(cc):
        alerts.append("⛈️ Thunderstorm")
    if is_other(nc) and not is_other(cc):
        alerts.append("⚠️ Severe weather")
    return (alerts, next_label)


def _hour_to_label(h: int) -> str:
    if h == 0:
        return "12a"
    if h < 12:
        return f"{h}a"
    if h == 12:
        return "12p"
    return f"{h - 12}p"


# ---------------------------------------------------------------------------
# Source #3 — current rain
# ---------------------------------------------------------------------------
def evaluate_current_rain(
    current_precip_mm_per_hr: float,
    last_broadcast_ts: int,
    debounce_seconds: int,
    now_ts: int | None = None,
) -> tuple[bool, bool]:
    """Return ``(should_broadcast, is_fresh_start)``.

    ``is_fresh_start`` is True if we haven't broadcast in >6h (or ever) —
    the message wording changes from "Heads up" to "Still raining".
    """
    if current_precip_mm_per_hr <= 0:
        return (False, False)
    now_ts = now_ts or int(time.time())
    since = now_ts - last_broadcast_ts
    if since < debounce_seconds:
        return (False, False)
    is_fresh_start = (last_broadcast_ts == 0) or (since > 6 * 3600)
    return (True, is_fresh_start)


# ---------------------------------------------------------------------------
# Source #4 — forecast-lead
# ---------------------------------------------------------------------------
def evaluate_forecast_lead(
    forecast: dict,
    min_rain_mm: float,
    now: datetime | None = None,
) -> tuple[int, float] | None:
    """Return ``(hours_ahead, max_precip_mm_in_window)`` or None if no rain coming."""
    now = now or datetime.now()
    h = forecast.get("hourly", {}) or {}
    times: list[str] = h.get("time", []) or []
    precip: list[Any] = h.get("precipitation", []) or []
    if not times or not precip:
        return None
    earliest_dt: datetime | None = None
    max_precip = 0.0
    for i, t in enumerate(times):
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except ValueError:
            continue
        p = (precip[i] if i < len(precip) else 0) or 0
        try:
            p = float(p)
        except (TypeError, ValueError):
            p = 0.0
        if p > max_precip:
            max_precip = p
        if p >= min_rain_mm and earliest_dt is None:
            secs_ahead = (dt - now).total_seconds()
            if secs_ahead >= -1800:
                earliest_dt = dt
    if earliest_dt is None:
        return None
    hours_ahead = max(1, round((earliest_dt - now).total_seconds() / 3600))
    return (hours_ahead, max_precip)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_once(
    config: AppConfig,
    lat: float,
    lon: float,
    location_label: str,
    mm: MeshMonitorClient | None = None,
    session: requests.Session | None = None,
    dry_run: bool | None = None,
) -> AlertRunResult:
    """Run all four sources once. Returns what was sent vs. suppressed."""
    sev = config.severe_alerter
    if not sev.enabled:
        return AlertRunResult(skipped_reason="severe_alerter_disabled")

    if dry_run is None:
        dry_run = os.environ.get("TEMPEST_MESH_DRY_RUN", "").lower() in ("1", "true", "yes")

    mm = mm or MeshMonitorClient(config)
    session = session or requests.Session()
    result = AlertRunResult()

    state_dir = sev.state_dir
    dedup_path = _state_path(state_dir, "nws-alert-dedup.json")
    cur_rain_ts_path = _state_path(state_dir, "current-rain-last.timestamp")
    forecast_lead_ts_path = _state_path(state_dir, "forecast-lead-last.timestamp")

    # --- 1) NWS ---
    nws_alerts = fetch_nws_alerts(lat, lon, sev.nws_user_agent, session=session)
    nws_alerts = dedup_alerts(nws_alerts, dedup_path, sev.dedup_ttl_hours)
    nws_messages: list[str] = []
    for a in nws_alerts:
        nws_messages.append(
            format_nws_alert_message(a["event"], a.get("expires"), a.get("headline"))
        )

    # --- 2) Open-Meteo transitions ---
    forecast = _fetch_open_meteo(
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,weather_code,wind_speed_10m,wind_gusts_10m,precipitation",
            "forecast_days": 2,
            "timezone": "auto",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
        },
        session=session,
    )
    transition_msg: str | None = None
    if forecast:
        alerts, next_label = detect_next_hour_transitions(forecast)
        if alerts:
            transition_msg = format_open_meteo_transition_message(
                location_label, alerts, next_label or "??"
            )

    # --- 3) current rain ---
    current = _fetch_open_meteo(
        {
            "latitude": lat,
            "longitude": lon,
            "current": "precipitation,weather_code,temperature_2m",
            "precipitation_unit": "mm",
            "timezone": "auto",
        },
        session=session,
    )
    current_rain_msg: str | None = None
    last_cur_ts = _read_timestamp(cur_rain_ts_path)
    if current:
        cur = current.get("current") or {}
        precip_mm = float(cur.get("precipitation") or 0)
        should_broadcast, is_fresh = evaluate_current_rain(
            precip_mm,
            last_cur_ts,
            sev.current_rain_debounce_seconds,
        )
        if should_broadcast:
            current_rain_msg = format_current_rain_message(
                location_label,
                precip_mm,
                sev.heavy_rain_mm_per_hr,
                is_fresh,
            )

    # --- 4) forecast lead ---
    forecast_lead_msg: str | None = None
    lead_forecast = _fetch_open_meteo(
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": "precipitation,weather_code",
            "forecast_hours": sev.forecast_lead_hours,
            "timezone": "auto",
            "precipitation_unit": "mm",
        },
        session=session,
    )
    last_lead_ts = _read_timestamp(forecast_lead_ts_path)
    if lead_forecast:
        eval_res = evaluate_forecast_lead(lead_forecast, sev.min_rain_mm)
        if eval_res is not None:
            hours_ahead, max_mm = eval_res
            now_ts = int(time.time())
            if (now_ts - last_lead_ts) >= sev.forecast_lead_debounce_seconds:
                forecast_lead_msg = format_forecast_lead_message(
                    location_label,
                    hours_ahead,
                    max_mm,
                    sev.heavy_rain_mm_per_hr,
                )

    # --- aggregate + suppression ---
    to_send: list[tuple[str, str]] = []  # (label, message)
    for msg in nws_messages:
        to_send.append(("nws", msg))

    if transition_msg and not nws_messages:
        to_send.append(("open_meteo_transition", transition_msg))

    if current_rain_msg:
        nws_has_rain = any(("Rain" in m) or ("Flood" in m) or ("🌧" in m) for m in nws_messages)
        if nws_has_rain:
            result.suppressed.append("current_rain (NWS has rain alert)")
        else:
            to_send.append(("current_rain", current_rain_msg))

    if forecast_lead_msg:
        if nws_messages:
            result.suppressed.append("forecast_lead (NWS active)")
        elif current_rain_msg:
            result.suppressed.append("forecast_lead (current rain just broadcast)")
        else:
            to_send.append(("forecast_lead", forecast_lead_msg))

    if not to_send:
        logger.info("No new hazards; nothing to broadcast.")
        return result

    # --- send ---
    now_ts = int(time.time())
    for label, msg in to_send:
        if dry_run:
            logger.info("[DRY-RUN] would send: %s", msg)
            print(f"[DRY-RUN] {msg}")
            result.sent.append(msg)
            continue
        ok = mm.post_message(msg, channel_index=sev.channel_index, label=f"severe-{label}")
        if ok:
            result.sent.append(msg)
            if label == "current_rain":
                _write_timestamp(cur_rain_ts_path, now_ts)
            elif label == "forecast_lead":
                _write_timestamp(forecast_lead_ts_path, now_ts)
        else:
            result.suppressed.append(f"{label} (post failed)")
        time.sleep(2)  # gentle pacing between mesh broadcasts

    return result
