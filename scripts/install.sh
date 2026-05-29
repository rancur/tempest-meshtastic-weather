#!/usr/bin/env bash
# tempest-meshtastic-weather installer.
# Idempotent. Safe to re-run. Asks the bare minimum and writes config.yaml + .env.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*"; }
bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
ask() {
  # ask <prompt> <var-name> [default]
  local prompt="$1" var="$2" default="${3:-}"
  local val
  if [[ -n "$default" ]]; then
    read -rp "$prompt [$default]: " val
    val="${val:-$default}"
  else
    read -rp "$prompt: " val
  fi
  printf -v "$var" '%s' "$val"
}

bold "tempest-meshtastic-weather — interactive installer"
echo "Repo root: $REPO_ROOT"
echo

# --- 1. Python venv -----------------------------------------------------------
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  red "python3 not found on PATH; install Python 3.10+ first."
  exit 2
fi

if [[ ! -d .venv ]]; then
  echo "Creating .venv via $PYTHON -m venv..."
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
echo "Installing package + deps..."
pip install --quiet -e ".[dev]"

green "  -> .venv ready, tempest-mesh CLI available"

# --- 2. config / env ---------------------------------------------------------
if [[ -f .env ]]; then
  echo
  bold "Detected existing .env — keeping it. Edit by hand or delete to re-run prompts."
else
  echo
  bold "Let's collect the bare minimum config. (You can edit .env later for everything else.)"

  ask "Tempest source [cloud/udp/garden_api]" TEMPEST_SOURCE cloud
  case "$TEMPEST_SOURCE" in
    cloud)
      ask "Tempest personal API token (https://tempestwx.com/settings/tokens)" TEMPEST_API_TOKEN
      ask "Tempest station ID (number, shown on tempestwx.com)" TEMPEST_STATION_ID
      ;;
    udp)
      echo "  (no token required for UDP — must run on the same LAN as the Tempest Hub)"
      TEMPEST_API_TOKEN=""
      TEMPEST_STATION_ID=""
      ;;
    garden_api)
      ask "garden_api URL (e.g. http://localhost:3402)" GARDEN_API_URL
      ask "shared X-Tempest-Token" TEMPEST_LOCAL_TOKEN
      TEMPEST_API_TOKEN=""
      TEMPEST_STATION_ID=""
      ;;
    *)
      red "unknown source: $TEMPEST_SOURCE"; exit 2 ;;
  esac

  ask "MeshMonitor URL" MESHMONITOR_URL "http://localhost:8080"
  ask "MeshMonitor API token (Yeraze/meshmonitor MM_API_TOKEN)" MESHMONITOR_API_TOKEN
  ask "Default channel index (0 = primary)" MESHMONITOR_CHANNEL_INDEX 0
  ask "Location strategy [fixed/from_meshmonitor]" LOCATION_STRATEGY fixed
  ask "Location label (short, ≤15 chars)" LOCATION_NAME "Home"
  if [[ "$LOCATION_STRATEGY" == "fixed" ]]; then
    ask "Location latitude (decimal degrees)" LOCATION_LAT
    ask "Location longitude (decimal degrees)" LOCATION_LON
    LOCATION_MESHMONITOR_NODE_ID=""
  else
    ask "MeshMonitor nodeId of your locally-attached radio (e.g. !12ab34cd)" LOCATION_MESHMONITOR_NODE_ID
    LOCATION_LAT=""
    LOCATION_LON=""
  fi

  ask "Enable severe-weather alerter? [true/false]" SEVERE_ALERTER_ENABLED true
  if [[ "$SEVERE_ALERTER_ENABLED" == "true" ]]; then
    ask "NWS User-Agent (must include contact, e.g. 'My Mesh (me@example.com)')" SEVERE_NWS_USER_AGENT "tempest-meshtastic-weather ($(whoami)@$(hostname -f 2>/dev/null || hostname))"
  else
    SEVERE_NWS_USER_AGENT="tempest-meshtastic-weather (disabled)"
  fi

  # write .env
  cat > .env <<EOF
TEMPEST_SOURCE=${TEMPEST_SOURCE}
TEMPEST_API_TOKEN=${TEMPEST_API_TOKEN:-}
TEMPEST_STATION_ID=${TEMPEST_STATION_ID:-}
GARDEN_API_URL=${GARDEN_API_URL:-}
TEMPEST_LOCAL_TOKEN=${TEMPEST_LOCAL_TOKEN:-}

MESHMONITOR_URL=${MESHMONITOR_URL}
MESHMONITOR_API_TOKEN=${MESHMONITOR_API_TOKEN}
MESHMONITOR_CHANNEL_INDEX=${MESHMONITOR_CHANNEL_INDEX}

LOCATION_STRATEGY=${LOCATION_STRATEGY}
LOCATION_NAME=${LOCATION_NAME}
LOCATION_LAT=${LOCATION_LAT:-}
LOCATION_LON=${LOCATION_LON:-}
LOCATION_MESHMONITOR_NODE_ID=${LOCATION_MESHMONITOR_NODE_ID:-}

SEVERE_ALERTER_ENABLED=${SEVERE_ALERTER_ENABLED}
SEVERE_NWS_USER_AGENT=${SEVERE_NWS_USER_AGENT}
EOF
  chmod 600 .env
  green "  -> wrote .env (permissions 600). Edit any time."
fi

if [[ ! -f config.yaml ]]; then
  cp config.example.yaml config.yaml
  green "  -> wrote config.yaml from example. Override anything via .env."
fi

# --- 3. doctor check ----------------------------------------------------------
echo
bold "Running 'tempest-mesh doctor' to verify Tempest + MeshMonitor connectivity..."
echo "(Will post a one-line test broadcast to your mesh.)"
read -rp "Run doctor now? [Y/n]: " run_doctor
run_doctor="${run_doctor:-Y}"
if [[ "$run_doctor" =~ ^[Yy]$ ]]; then
  set -a; source .env; set +a
  tempest-mesh doctor || { red "doctor failed — fix the errors above before continuing."; exit 1; }
fi

# --- 4. optional systemd ------------------------------------------------------
echo
bold "Install systemd timers so this runs hourly + every 30min?"
echo "(Requires sudo. Skip if you'd rather wire your own cron / Docker.)"
read -rp "Install systemd timers? [y/N]: " want_systemd
want_systemd="${want_systemd:-N}"
if [[ "$want_systemd" =~ ^[Yy]$ ]]; then
  if [[ "$EUID" -ne 0 ]]; then
    echo "Re-running install as sudo for systemd..."
    sudo -E env "PATH=$PATH" tempest-mesh install-systemd --working-dir "$REPO_ROOT"
  else
    tempest-mesh install-systemd --working-dir "$REPO_ROOT"
  fi
fi

echo
green "Done. Try: tempest-mesh post-conditions --dry-run"
