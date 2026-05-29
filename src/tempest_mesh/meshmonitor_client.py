"""MeshMonitor HTTP API client.

MeshMonitor (https://github.com/Yeraze/meshmonitor) exposes a small HTTP API:

- ``POST /api/v1/messages``  — submit text to a channel.
- ``GET  /api/v1/nodes``     — list nodes (used by ``from_meshmonitor`` location).

Backend choice (Postgres vs SQLite) is abstracted away by MeshMonitor itself —
this client speaks the same HTTP shape regardless of how MeshMonitor stores data.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from .config import AppConfig

logger = logging.getLogger(__name__)


@dataclass
class MeshNode:
    """Subset of a MeshMonitor node row we care about for location."""

    node_id: str
    lat: float | None
    lon: float | None
    role: int | None
    hops_away: int | None
    position_timestamp: int | None  # epoch ms (MeshMonitor convention)
    last_heard: int | None  # epoch seconds


class MeshMonitorError(Exception):
    """Raised when MeshMonitor is unreachable or returns an error envelope."""


class MeshMonitorClient:
    """Thin client over MeshMonitor's HTTP API + a retry envelope."""

    def __init__(self, config: AppConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()
        self.url = config.meshmonitor.url.rstrip("/")
        self.token = config.meshmonitor.api_token
        self.max_attempts = config.meshmonitor.retry_max_attempts
        self.sleep_seconds = config.meshmonitor.retry_sleep_seconds
        self.timeout_seconds = config.meshmonitor.retry_timeout_seconds

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    # --- sending ------------------------------------------------------------
    def post_message(
        self,
        text: str,
        channel_index: int | None = None,
        label: str = "message",
    ) -> bool:
        """Post text to MeshMonitor with retry. Returns True on success."""
        if not self.token:
            raise MeshMonitorError("MESHMONITOR_API_TOKEN is required to post messages")
        ch = channel_index if channel_index is not None else self.config.meshmonitor.channel_index
        payload = {"text": text, "channel": ch}

        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            logger.info(
                "Sending %s (attempt %d/%d) -> %s",
                label,
                attempt,
                self.max_attempts,
                f"{self.url}/api/v1/messages",
            )
            try:
                resp = self.session.post(
                    f"{self.url}/api/v1/messages",
                    headers=self._headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                try:
                    resp.raise_for_status()
                    body = resp.json()
                except (ValueError, requests.HTTPError) as e:
                    logger.warning(
                        "Attempt %d non-2xx or non-JSON: %s; raw=%s",
                        attempt,
                        e,
                        resp.text[:200],
                    )
                    body = None

                if body and body.get("success") is True:
                    logger.info("OK: %s sent on attempt %d", label, attempt)
                    return True

                logger.warning("Attempt %d failed: body=%s", attempt, body)
            except requests.RequestException as e:
                last_exc = e
                logger.warning("Attempt %d exception: %s", attempt, e)

            if attempt < self.max_attempts:
                logger.info(
                    "Sleeping %ds before retry (likely transient MeshMonitor disconnect)",
                    self.sleep_seconds,
                )
                time.sleep(self.sleep_seconds)

        logger.error(
            "FAIL: all %d attempts failed; giving up on %s (last=%s)",
            self.max_attempts,
            label,
            last_exc,
        )
        return False

    # --- nodes --------------------------------------------------------------
    def list_nodes(self) -> list[MeshNode]:
        if not self.token:
            raise MeshMonitorError("MESHMONITOR_API_TOKEN is required to list nodes")
        resp = self.session.get(
            f"{self.url}/api/v1/nodes",
            headers=self._headers,
            timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()
        rows: list[dict[str, Any]] = payload.get("data", []) if isinstance(payload, dict) else []
        return [
            MeshNode(
                node_id=str(n.get("nodeId") or ""),
                lat=n.get("latitude"),
                lon=n.get("longitude"),
                role=n.get("role"),
                hops_away=n.get("hopsAway"),
                position_timestamp=n.get("positionTimestamp"),
                last_heard=n.get("lastHeard"),
            )
            for n in rows
        ]

    def resolve_local_node_position(
        self,
        target_node_id: str = "",
    ) -> tuple[float, float, int] | None:
        """Return ``(lat, lon, age_seconds)`` for the local node, or None.

        Resolution strategy:

          1. Match by ``nodeId == target_node_id`` (most reliable).
          2. Fall back to: hopsAway is None AND role == 0 (CLIENT, locally attached).
          3. Fall back to: first node with a valid GPS fix (warn).
        """
        nodes = self.list_nodes()

        def has_pos(n: MeshNode) -> bool:
            return n.lat is not None and n.lon is not None

        chosen: MeshNode | None = None
        if target_node_id:
            for n in nodes:
                if n.node_id == target_node_id and has_pos(n):
                    chosen = n
                    break
        if chosen is None:
            for n in nodes:
                if n.hops_away is None and n.role == 0 and has_pos(n):
                    chosen = n
                    logger.warning(
                        "nodeId %r not found; using local client %s",
                        target_node_id,
                        n.node_id,
                    )
                    break
        if chosen is None:
            for n in nodes:
                if has_pos(n):
                    chosen = n
                    logger.warning(
                        "no local node found; falling back to first GPS node %s",
                        n.node_id,
                    )
                    break
        if chosen is None or chosen.lat is None or chosen.lon is None:
            return None

        now_s = int(time.time())
        if chosen.position_timestamp:
            age = max(0, now_s - int(chosen.position_timestamp / 1000))
        elif chosen.last_heard:
            age = max(0, now_s - int(chosen.last_heard))
        else:
            age = -1
        return (chosen.lat, chosen.lon, age)
