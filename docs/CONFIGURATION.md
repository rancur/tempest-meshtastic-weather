# Configuration reference

Every key in `config.yaml` documented here. Environment variables override YAML — so you can rotate tokens without touching a file.

## `tempest:` — where weather data comes from

| Key | Env override | Default | Notes |
|---|---|---|---|
| `source` | `TEMPEST_SOURCE` | `cloud` | One of `cloud`, `udp`, `garden_api`. |
| `api_token` | `TEMPEST_API_TOKEN` | — | Required for `cloud`. Get from https://tempestwx.com/settings/tokens. |
| `station_id` | `TEMPEST_STATION_ID` | — | Required for `cloud`. Find at https://tempestwx.com/station/<id>. |
| `udp_port` | `TEMPEST_UDP_PORT` | `50222` | Tempest Hub UDP broadcast port. Don't change unless you've remapped it. |
| `udp_listen_seconds` | `TEMPEST_UDP_LISTEN_SECONDS` | `60` | How long each `post-conditions` run waits for an `obs_st` packet. Default Hub interval is 60s, so 60 is the right floor. |
| `garden_api_url` | `GARDEN_API_URL` | — | URL of a shared HTTP listener (advanced — see [shared-listener](#shared-listener-source-garden_api) below). |
| `local_token` | `TEMPEST_LOCAL_TOKEN` | — | `X-Tempest-Token` header sent to the shared listener. |

### Pick a source

- **`cloud`** (default) — Works from anywhere. Uses the WeatherFlow REST API. You need a personal token. Costs nothing.
- **`udp`** — Best accuracy, lowest latency. Requires the install host to be on the same LAN broadcast domain as the Tempest Hub. The install process binds UDP `:50222`, so you cannot run two things on the same host that want this socket.
- **`garden_api`** — If you already have a separate service that owns the UDP socket (because another integration needs it too), point at that service over HTTP. Documented for completeness; most users want `cloud` or `udp`.

#### Shared-listener (source `garden_api`)

When multiple processes need Tempest data on the same host, one process owns the UDP socket and exposes the latest observation over HTTP. The convention this package supports:

```
GET ${GARDEN_API_URL}/api/sensors/tempest-local
Header: X-Tempest-Token: <shared-secret>

Response: {"receiving": true, "observation": { temperature_f, humidity, wind_avg_mph, wind_direction, ... }}
```

Any HTTP service that matches this shape works.

## `meshmonitor:` — where messages go

| Key | Env override | Default | Notes |
|---|---|---|---|
| `url` | `MESHMONITOR_URL` | `http://localhost:8080` | MeshMonitor HTTP API base. |
| `api_token` | `MESHMONITOR_API_TOKEN` | — | Required. Set MeshMonitor's `MM_API_TOKEN`. |
| `channel_index` | `MESHMONITOR_CHANNEL_INDEX` | `0` | Default channel for `post-conditions`. `0` = primary. |
| `retry_max_attempts` | — | `3` | Per-message retry envelope. |
| `retry_sleep_seconds` | — | `30` | Sleep between attempts. The mesh hop layer is bursty — 30s lets the radio recover. |
| `retry_timeout_seconds` | — | `15` | Per-request HTTP timeout. |

## `location:` — where the weather IS, geographically

| Key | Env override | Default | Notes |
|---|---|---|---|
| `strategy` | `LOCATION_STRATEGY` | `fixed` | `fixed` or `from_meshmonitor`. |
| `name` | `LOCATION_NAME` | `Home` | Short label shown in the message (≤15 chars by default). |
| `lat` | `LOCATION_LAT` | — | Required when `strategy: fixed`. Decimal degrees. |
| `lon` | `LOCATION_LON` | — | Required when `strategy: fixed`. Decimal degrees. |
| `meshmonitor_node_id` | `LOCATION_MESHMONITOR_NODE_ID` | — | Required when `strategy: from_meshmonitor`. Format `!12ab34cd`. Run `tempest-mesh doctor` to see candidate nodeIds. |
| `position_max_age_seconds` | `LOCATION_POSITION_MAX_AGE_SECONDS` | `86400` | Refuse to broadcast if MeshMonitor's last GPS fix for this node is older than this. Stops a mobile rig from advertising last week's location. |

### Fixed vs mobile

- **Stationary station (home backyard, fixed install)** -> `strategy: fixed`. Set `lat`, `lon`, `name` once and forget.
- **Mobile rig (RV, road-trip Pi, packet truck)** -> `strategy: from_meshmonitor`. The poster reads the GPS of your locally-attached Meshtastic node every run and uses that. Used together with the severe-weather alerter, this means alerts target wherever you are right now.

## `message:` — formatting knobs

| Key | Default | Notes |
|---|---|---|
| `units` | `imperial` | `imperial` -> °F + mph; `metric` -> °C + km/h. |
| `location_label_max_chars` | `15` | Truncate location name to this. |
| `message_max_chars` | `200` | Hard byte cap on every broadcast. Emoji-safe truncation. |

## `severe_alerter:` — the heads-up watcher

Four-source cascade: NWS (#1) > Open-Meteo next-hour transition (#2) > current rain (#3) > 4-6h forecast lead (#4).

| Key | Env override | Default | Notes |
|---|---|---|---|
| `enabled` | `SEVERE_ALERTER_ENABLED` | `true` | Master switch. |
| `nws_user_agent` | `SEVERE_NWS_USER_AGENT` | placeholder | NWS REQUIRES a unique UA with contact info. Format: `MyApp (me@example.com)`. |
| `channel_index` | `SEVERE_ALERT_CHANNEL_INDEX` | `0` | Channel severe alerts broadcast on. Usually primary. |
| `dedup_ttl_hours` | `SEVERE_DEDUP_TTL_HOURS` | `6` | NWS alert dedupe window. Same alert id is silenced for this long. |
| `forecast_lead_hours` | `SEVERE_FORECAST_LEAD_HOURS` | `6` | How far out the camp-prep lead-time signal looks. |
| `forecast_lead_debounce_seconds` | `SEVERE_FORECAST_LEAD_DEBOUNCE_SECONDS` | `3600` | Forecast-lead messages will fire at most once per hour. |
| `heavy_rain_mm_per_hr` | `SEVERE_HEAVY_RAIN_MM_PER_HR` | `2.0` | At or above this, the message upgrades from "rain" to "⚠️ Heavy rain". |
| `min_rain_mm` | `SEVERE_MIN_RAIN_MM` | `0.1` | Hourly precip below this is treated as "no rain" (filter drizzle noise). |
| `current_rain_debounce_seconds` | `SEVERE_CURRENT_RAIN_DEBOUNCE_SECONDS` | `1800` | During sustained rain, broadcast at most once per 30 min. |
| `state_dir` | `SEVERE_STATE_DIR` | `./state` | Heartbeat + dedup files live here. |

### Suppression rules

- An NWS rain alert suppresses Source #3 (current rain) — we don't need to double-narrate.
- Any NWS alert OR a Source #3 broadcast in the last 30 min suppresses Source #4 (forecast lead) — recent narrations win.

### Outside the USA

NWS only covers US points. Outside the US, set `SEVERE_ALERTER_ENABLED=false` and rely on Open-Meteo sources #2-#4, OR override the watcher to consult a local met-office API and submit a PR.

## `logging:`

| Key | Env override | Default | Notes |
|---|---|---|---|
| `level` | `TEMPEST_MESH_LOG_LEVEL` | `INFO` | Standard Python log levels. |
