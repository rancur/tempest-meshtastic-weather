# tempest-meshtastic-weather

[![CI](https://github.com/rancur/tempest-meshtastic-weather/actions/workflows/ci.yml/badge.svg)](https://github.com/rancur/tempest-meshtastic-weather/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

> Auto-broadcast Tempest WeatherFlow conditions + severe-weather alerts to your Meshtastic mesh via MeshMonitor.

```
WX Home 9AM: 53°F Sunny ☀️ | Wind 6 mph E | RH 93% | UV 0
```

```
⚠️ Heavy rain expected at Camp X in ~3h. Secure your camp. ⛺☔
```

```
⚠️ Severe Thunderstorm Warning until 6:30pm – damaging winds and large hail expected
```

This package turns a [Tempest WeatherFlow](https://weatherflow.com/tempest-home-weather-system/) station into a continuous source of mesh-friendly weather updates for your Meshtastic network — including a separate alerter that broadcasts severe weather (NWS) and incoming-rain heads-ups (Open-Meteo) ahead of the storm so people on the mesh have time to react.

Battle-tested on two production deployments — a fixed home Meshtastic base station and a mobile road-trip rig — both running MeshMonitor.

---

## Why

Your Meshtastic mesh is a community-of-presence radio. If you've got accurate weather data, your mesh should have it too:

- **Day-to-day:** people on your channel know what the sky is doing right now — temperature, wind, humidity, UV — without having to pull up an app.
- **Storm prep:** when severe weather is incoming, the mesh hears about it BEFORE it arrives. The forecast-lead source gives 4-6 hours of camp-prep time. The NWS source picks up tornado / flash-flood / severe-thunderstorm warnings for your point. The current-rain source confirms what people can already feel.
- **Off-grid coverage:** all of this works when cell coverage drops, as long as the host running this bridge can still reach the internet (Starlink, RV hotspot, etc).

---

## What you need

- **A Tempest WeatherFlow station** — either:
  - A personal API token + station ID from https://tempestwx.com/settings/tokens (works anywhere), OR
  - The Tempest Hub on the same LAN as your install host (UDP source, no token).
- **A MeshMonitor instance** — https://github.com/Yeraze/meshmonitor — with `MM_API_TOKEN` set, reachable from the install host.
- **Python 3.10+**

Backend choice (Postgres vs SQLite) is invisible to this package — MeshMonitor abstracts it. Whichever you've picked, this works.

---

## Quick install

```bash
git clone https://github.com/rancur/tempest-meshtastic-weather.git
cd tempest-meshtastic-weather
./scripts/install.sh
```

The installer:

1. Builds a `.venv`, installs the package, registers the `tempest-mesh` CLI.
2. Prompts for the bare minimum: Tempest source + token, MeshMonitor URL + token, location strategy.
3. Runs `tempest-mesh doctor` to verify both sides reachable + posts a one-line test broadcast.
4. Optionally installs + enables systemd timers (hourly conditions + 30-min severe watcher).

Full walkthrough in [docs/INSTALL.md](docs/INSTALL.md).

---

## Detailed config

Every knob is documented in [docs/CONFIGURATION.md](docs/CONFIGURATION.md). The shape:

```yaml
tempest:
  source: cloud           # cloud | udp | garden_api
  api_token: "${TEMPEST_API_TOKEN}"
  station_id: "${TEMPEST_STATION_ID}"

meshmonitor:
  url: http://localhost:8080
  api_token: "${MESHMONITOR_API_TOKEN}"
  channel_index: 0        # 0 = primary

location:
  strategy: fixed         # fixed | from_meshmonitor
  name: Home
  lat: 33.5
  lon: -112.2

severe_alerter:
  enabled: true
  nws_user_agent: "tempest-meshtastic-weather (you@example.com)"
  channel_index: 0
```

Environment variables override YAML — rotate tokens without touching files.

---

## What it broadcasts

### Current conditions (hourly)

```
WX Home 9AM: 53°F Sunny ☀️ | Wind 6 mph E | RH 93% | UV 0
WX Home 3PM: 92°F Sunny ☀️ | Wind 12 mph SSW | RH 18% | UV 11
WX Home 11PM: 72°F Clear 🌙 | Wind 3 mph N | RH 35%
```

- Imperial units by default (configurable to metric).
- Condition + emoji derived from solar radiation, illuminance, rain rate, and lightning count — accurate enough to match the Tempest app's own labels in most regimes.
- One line, ≤200 bytes (Meshtastic-friendly), UTF-8-byte-safe truncation that won't emit a half-emoji.

### Severe-weather alerts (every 30 min)

Four cascading sources:

1. **NWS active alerts** (USA) — tornado, severe thunderstorm, flash flood, etc. Most authoritative; dedup'd 6h.
2. **Open-Meteo "starting in next hour"** — picks up wind / rain / thunderstorm conditions starting in the next hour but not present now. Suppressed if NWS already broadcast.
3. **Current rain** — fires whenever local precipitation > 0, debounced to once per 30 min. Suppressed if NWS has a rain alert active.
4. **4-6h forecast lead** — "rain expected in ~4h, secure your camp" heads-up. Long enough to give people time to get back to base. Suppressed if NWS active OR Source #3 broadcast in last 30 min.

Example outputs:

```
⚠️ Severe Thunderstorm Warning until 6:30pm – damaging winds and large hail expected
⚠️ Camp X – In ~1 hr (4p): 💨 Wind 25+ mph | 🌧️ Rain
Heads up — rain at Camp X. 0.6mm/hr ☔
⚠️ Heavy rain expected at Camp X in ~3h. Secure your camp. ⛺☔
```

---

## Severe-weather alerter — how the four sources fit

The cascade is opinionated, NOT additive: each source's purpose is to surface info the previous source would have missed.

| Source | When it fires | When suppressed |
|---|---|---|
| **#1 NWS** | Severe/extreme active alert at your point | Already broadcast this `id` within `dedup_ttl_hours` (default 6h) |
| **#2 Open-Meteo transition** | Rain / wind / tstorm starts in the next hour but not present now | Any NWS alert this run |
| **#3 Current rain** | Local precipitation > 0 mm/hr | NWS has a Rain / Flood alert |
| **#4 4-6h forecast lead** | Any hour in the window has precip ≥ `min_rain_mm` | NWS active, OR #3 fired within `current_rain_debounce_seconds` (default 30 min) |

All thresholds and debounce windows are configurable — see [docs/CONFIGURATION.md](docs/CONFIGURATION.md#severe_alerter--the-heads-up-watcher).

---

## Backends

**MeshMonitor abstracts storage**, so this package speaks a single HTTP interface regardless of how MeshMonitor stores data:

- **Postgres-backed MeshMonitor** — typical for fixed home / production installs with multi-week history retention.
- **SQLite-backed MeshMonitor** — typical for mobile / RV / single-Pi installs.

We talk to `POST /api/v1/messages` + `GET /api/v1/nodes` — both backends serve identical responses. No backend-specific configuration needed in this package.

---

## CLI

```
tempest-mesh post-conditions            # one-shot — fetch + post
tempest-mesh post-conditions --dry-run  # format + print, do not send
tempest-mesh watch-forecast             # run severe-weather watcher once
tempest-mesh watch-forecast --dry-run   # show what WOULD be broadcast
tempest-mesh doctor                     # verify Tempest + MeshMonitor + test ping
tempest-mesh test-format                # one-shot format-and-print (no send)
tempest-mesh install-systemd            # copy + enable systemd timer units
```

---

## Architecture

```
+------------+   poll    +---------------+   POST /api/v1/messages   +-------------+
|  Tempest   | --------> | tempest-mesh  | ------------------------> | MeshMonitor | --> Meshtastic mesh
|  (cloud /  |  fetch    |  (this pkg)   |                           |  (Yeraze)   |
|   UDP /    |           |               |   GET /api/v1/nodes       |             |
| garden-api)|           |               | <------------------------ |             |
+------------+           +---------------+    (for location)         +-------------+
                                ^
                                |
                          + NWS API (severe)
                          + Open-Meteo (forecasts)
                          + Nominatim (reverse geocode)
```

Two independent runners share the same client code:

- **conditions_poster** — one-shot per cron tick. Formats and posts current conditions.
- **severe_weather_alerter** — runs the four-source cascade with dedup + suppression. Designed to be safe to fire on a tight schedule (every 30 min) because it only broadcasts on change.

---

## Roadmap

Open contribution invite. Known wants:

- [ ] Built-in Dockerfile + GitHub-published image.
- [ ] EU-equivalent of NWS (DWD / Met Office / etc) for the severe alerter.
- [ ] Battery / hub-health broadcasts (Tempest reports both — could be a separate optional channel).
- [ ] Per-channel routing rules (e.g. conditions on Weather channel, severe on Primary).
- [ ] Pluggable forecast source (default Open-Meteo, allow swapping in Pirate Weather / NWS forecast).
- [ ] OpenAPI client for MeshMonitor (currently raw HTTP).

PRs welcome. The test suite mocks all network calls, so any contribution can be verified locally with `pytest -q`.

---

## Credits

- [WeatherFlow](https://weatherflow.com/) for the Tempest station + open API.
- [Yeraze/meshmonitor](https://github.com/Yeraze/meshmonitor) for the MeshMonitor HTTP API this depends on.
- [Meshtastic](https://meshtastic.org/) for the off-grid mesh that makes this useful.
- [Open-Meteo](https://open-meteo.com/) for the free forecast API the severe alerter falls back to.
- [NWS](https://www.weather.gov/documentation/services-web-api) for the authoritative severe-weather alerts (USA).

---

## License

[MIT](LICENSE)
