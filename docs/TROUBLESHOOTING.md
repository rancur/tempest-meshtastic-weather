# Troubleshooting

Run `tempest-mesh doctor` first — it isolates each side independently.

---

### 1. `WeatherFlow returned 401`

Your `TEMPEST_API_TOKEN` is wrong or expired. Get a fresh personal token from https://tempestwx.com/settings/tokens. Tokens are per-user, not per-station.

### 2. `WeatherFlow returned 404` for station

`TEMPEST_STATION_ID` doesn't match a station this token can access. Verify the ID at https://tempestwx.com/ — it appears in the URL of your station page (`/station/<ID>`).

### 3. `no obs_st packet on UDP :50222 within 60s`

UDP source can't see Hub broadcasts. Common causes:

- Install host is on a different VLAN / subnet than the Hub. UDP broadcasts don't cross subnets without router help. Switch to `TEMPEST_SOURCE=cloud`, OR move the install host onto the same broadcast domain.
- Another process already owns the socket (e.g. Home Assistant's Tempest integration in local-only mode). Either disable the other listener, or switch to `garden_api` source.
- Hub is asleep / unpowered. Check the Hub light.

### 4. `MESHMONITOR_API_TOKEN is required`

You didn't set `MESHMONITOR_API_TOKEN` in `.env` or `config.yaml`. Set MeshMonitor's `MM_API_TOKEN` env var (in its compose file) AND mirror it here.

### 5. MeshMonitor returns 401

Token mismatch between MeshMonitor's `MM_API_TOKEN` and the one you configured here. They have to match exactly.

### 6. MeshMonitor returns 200 but `{"success": false}`

MeshMonitor accepted the HTTP request but the underlying Meshtastic node refused or wasn't reachable. The retry envelope will give it three tries 30s apart. If you see this constantly:

- Check MeshMonitor's logs — is the radio connected?
- Verify the channel index you're posting on exists on that node.

### 7. `no local node with GPS` (severe alerter)

You're using `LOCATION_STRATEGY=from_meshmonitor` but:

- The `LOCATION_MESHMONITOR_NODE_ID` you set doesn't appear in MeshMonitor's `/api/v1/nodes`, AND
- There's no other node with `hopsAway=null` + `role=0` (locally-attached client), AND
- No node has reported a GPS fix.

Run `tempest-mesh doctor` — it dumps every node's `nodeId / lat / lon / role / hops` so you can pick the right one.

### 8. `position stale (Xs)` — broadcast skipped

Your local node hasn't reported a GPS fix in `LOCATION_POSITION_MAX_AGE_SECONDS` (default 86400 = 24h). This is intentional: if your mobile rig is sitting in a garage with no GPS, you don't want to keep broadcasting "weather at last week's location". Either:

- Move so your node gets a fresh fix, OR
- Bump `LOCATION_POSITION_MAX_AGE_SECONDS` if the staleness is acceptable.

### 9. NWS is silent on everything

NWS only covers US points. Outside the US, sources #2-#4 (Open-Meteo) still work — set `SEVERE_ALERTER_ENABLED=true` and ignore the NWS path.

### 10. Severe alerter broadcasts the same NWS alert twice

The dedupe ID write failed (state dir not writable, etc). Check that `SEVERE_STATE_DIR` exists and is writable by the user the unit runs as. Default is `./state` — make sure your working directory matches.

### 11. Messages get truncated mid-emoji

Your `message_max_chars` is too low. The default 200 is a Meshtastic LoRa-friendly default. The truncator is UTF-8-byte-aware and won't emit a half-emoji — but it will drop the emoji entirely. Bump `message_max_chars` if you've got headroom.

### 12. systemd timer fires but the unit fails

```
journalctl -u tempest-mesh-conditions.service -n 50
```

Look for: `ModuleNotFoundError` (venv not active — re-run installer), permission errors on `state/` (chown to the unit's user), or `EnvironmentFile` not found (path mismatch — re-run `install-systemd --working-dir`).

### 13. Open-Meteo returns truncated JSON intermittently

This is a real, observed failure mode on flaky uplinks. The retry envelope handles it. If you see it constantly, increase `MM_OPEN_METEO_TIMEOUT` (env override) to give slow links more headroom.

### 14. Conditions broadcast says "N/A" for temperature

The Tempest source returned a payload without `air_temperature`. Common causes:

- `garden_api` source returned an empty `observation` (UDP listener hadn't received a packet yet — wait one report interval).
- WeatherFlow returned an outage / stale obs.

Re-run `tempest-mesh post-conditions` once; if it persists, hit the underlying source directly with `curl` to confirm what it's returning.
