#!/usr/bin/env bash
# test-install.sh — sanity-check that a fresh clone installs + boots + tests cleanly.
# Designed to be hermetic: no real network calls, no real Tempest token.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-python3}"

# 1. Make a throwaway venv
VENV_DIR="$(mktemp -d)/venv"
echo "[1/4] Creating throwaway venv at $VENV_DIR"
"$PYTHON" -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip

# 2. Install the package
echo "[2/4] Installing tempest-meshtastic-weather + dev extras"
pip install --quiet -e ".[dev]"

# 3. Confirm the CLI registered + --help works
echo "[3/4] Smoke-test CLI"
tempest-mesh --version
tempest-mesh --help >/dev/null

# 4. Run the unit tests (these mock all network calls — no Tempest token needed)
echo "[4/4] Running unit tests"
pytest -q

echo
echo "OK — fresh-install path is healthy. Cleaning up $VENV_DIR"
deactivate
rm -rf "$VENV_DIR"
