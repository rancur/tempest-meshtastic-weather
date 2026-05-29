# Install guide

Three install paths, in order of "easiest" to "most flexible":

1. **Interactive installer** — `./scripts/install.sh`. Asks for the bare minimum and writes a working config.
2. **Manual venv + pip install** — for people who want to wire systemd / Docker themselves.
3. **Docker** — drop-in container, env-driven config.

---

## 1. Interactive installer (recommended)

```bash
git clone https://github.com/rancur/tempest-meshtastic-weather.git
cd tempest-meshtastic-weather
./scripts/install.sh
```

The installer:

- Creates a `.venv/` with the package + dev deps installed (editable).
- Prompts for Tempest source (cloud / udp / garden_api), MeshMonitor URL + token, and location strategy.
- Writes `.env` (mode 600) + copies `config.example.yaml` -> `config.yaml`.
- Optionally runs `tempest-mesh doctor` to verify both sides are reachable + posts a one-line test broadcast.
- Optionally installs + enables systemd timers (hourly conditions + 30-min severe-weather watcher).

Re-run safe: it never overwrites an existing `.env` or `config.yaml`.

---

## 2. Manual install

### Requirements

- Python 3.10+
- A reachable MeshMonitor instance (https://github.com/Yeraze/meshmonitor) with `MM_API_TOKEN` set.
- One of:
  - A WeatherFlow personal API token + station ID (cloud source), or
  - The Tempest Hub on the same LAN as the install host (UDP source).

### Steps

```bash
git clone https://github.com/rancur/tempest-meshtastic-weather.git
cd tempest-meshtastic-weather
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp config.example.yaml config.yaml
cp .env.example .env
# Edit .env (or config.yaml) with your tokens.
tempest-mesh doctor
```

When `doctor` passes, you can run:

```bash
tempest-mesh post-conditions          # one-shot
tempest-mesh watch-forecast           # one-shot severe-weather watcher
tempest-mesh post-conditions --dry-run  # print formatted message, do not send
```

### Wire to systemd

After the manual install:

```bash
sudo tempest-mesh install-systemd --working-dir "$PWD"
systemctl list-timers | grep tempest-mesh
journalctl -u tempest-mesh-conditions.service -f
```

The unit templates live in `systemd/` if you want to edit them before install.

### Wire to cron (if you'd rather not use systemd)

```cron
# Hourly conditions broadcast
0 * * * * cd /opt/tempest-meshtastic-weather && set -a; . .env; set +a; .venv/bin/tempest-mesh post-conditions >> /var/log/tempest-mesh.log 2>&1

# Severe-weather watcher every 30 min
*/30 * * * * cd /opt/tempest-meshtastic-weather && set -a; . .env; set +a; .venv/bin/tempest-mesh watch-forecast >> /var/log/tempest-mesh-severe.log 2>&1
```

---

## 3. Docker

A minimal Dockerfile is not currently shipped — `pyproject` packages cleanly and there are no native deps. PRs welcome.

Suggested usage if you build your own image:

```Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
ENV TEMPEST_SOURCE=cloud
ENV TEMPEST_MESH_LOG_LEVEL=INFO
ENTRYPOINT ["tempest-mesh"]
CMD ["post-conditions"]
```

Then either schedule the container externally (cron / kubernetes CronJob) or wrap in a shell loop.

---

## Troubleshooting an install

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — every failure mode the installer has produced is documented there.
