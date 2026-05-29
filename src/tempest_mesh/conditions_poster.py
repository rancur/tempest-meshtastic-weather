"""Current-conditions broadcaster.

Pulls a Tempest observation, formats it as a short message, posts to MeshMonitor.
Resolves the broadcast location either from a fixed config value or by reading
the GPS of the locally-attached Meshtastic node (for mobile setups).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import AppConfig
from .meshmonitor_client import MeshMonitorClient
from .message_formatter import format_conditions_message
from .tempest_client import TempestClient

logger = logging.getLogger(__name__)


@dataclass
class PostResult:
    success: bool
    message: str | None
    reason: str | None = None


def resolve_location(config: AppConfig, mm: MeshMonitorClient) -> tuple[str, str | None]:
    """Resolve ``(location_name, skip_reason)`` per config strategy."""
    loc_cfg = config.location
    if loc_cfg.strategy == "fixed":
        return (loc_cfg.name, None)
    # from_meshmonitor
    pos = mm.resolve_local_node_position(loc_cfg.meshmonitor_node_id)
    if pos is None:
        return (loc_cfg.name, "no_local_node_with_gps")
    _, _, age = pos
    if age >= 0 and age > loc_cfg.position_max_age_seconds:
        return (loc_cfg.name, f"stale_position_age_{age}s")
    return (loc_cfg.name, None)


def post_current_conditions(
    config: AppConfig,
    tempest: TempestClient | None = None,
    mm: MeshMonitorClient | None = None,
    dry_run: bool = False,
) -> PostResult:
    """One-shot: fetch Tempest, format, post (or print) to MeshMonitor."""
    tempest = tempest or TempestClient(config)
    mm = mm or MeshMonitorClient(config)

    location, skip_reason = resolve_location(config, mm)
    if skip_reason:
        logger.info("skipping conditions post: %s", skip_reason)
        return PostResult(success=False, message=None, reason=skip_reason)

    obs = tempest.fetch()
    message = format_conditions_message(obs, location, config.message)
    logger.info("formatted message: %s", message)

    if dry_run:
        print(message)
        return PostResult(success=True, message=message, reason="dry_run")

    ok = mm.post_message(message, label="conditions")
    return PostResult(
        success=ok,
        message=message,
        reason=None if ok else "meshmonitor_post_failed",
    )
