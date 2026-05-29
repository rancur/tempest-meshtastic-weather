"""``tempest-mesh`` CLI."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

import click

from . import __version__
from .conditions_poster import post_current_conditions, resolve_location
from .config import load_config
from .meshmonitor_client import MeshMonitorClient
from .severe_weather_alerter import run_once as run_severe_once
from .tempest_client import TempestClient, TempestFetchError


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to config.yaml. Defaults to ./config.yaml if present, else env-only.",
)
@click.version_option(version=__version__, prog_name="tempest-mesh")
@click.pass_context
def cli(ctx: click.Context, config_path: Path | None) -> None:
    """Auto-broadcast Tempest WeatherFlow weather to your Meshtastic mesh."""
    cfg = load_config(config_path)
    _setup_logging(cfg.logging.level)
    ctx.ensure_object(dict)
    ctx.obj["config"] = cfg


@cli.command("post-conditions")
@click.option("--dry-run", is_flag=True, help="Format + print only; do not send.")
@click.pass_context
def post_conditions_cmd(ctx: click.Context, dry_run: bool) -> None:
    """One-shot: fetch current Tempest conditions and post to MeshMonitor."""
    cfg = ctx.obj["config"]
    result = post_current_conditions(cfg, dry_run=dry_run)
    if not result.success and not dry_run:
        click.echo(f"failed: {result.reason}", err=True)
        sys.exit(1)


@cli.command("watch-forecast")
@click.option("--dry-run", is_flag=True, help="Print what would be broadcast; do not send.")
@click.pass_context
def watch_forecast_cmd(ctx: click.Context, dry_run: bool) -> None:
    """Run the severe-weather alerter once (designed to be called by a timer)."""
    cfg = ctx.obj["config"]
    mm = MeshMonitorClient(cfg)

    # Resolve lat/lon for the alert sources
    if cfg.location.strategy == "fixed":
        if cfg.location.lat is None or cfg.location.lon is None:
            click.echo(
                "fixed location strategy requires LOCATION_LAT + LOCATION_LON",
                err=True,
            )
            sys.exit(2)
        lat, lon = cfg.location.lat, cfg.location.lon
    else:
        pos = mm.resolve_local_node_position(cfg.location.meshmonitor_node_id)
        if pos is None:
            click.echo("no local node with GPS — skipping severe-alert run", err=True)
            sys.exit(0)
        lat, lon, age = pos
        if age >= 0 and age > cfg.location.position_max_age_seconds:
            click.echo(f"position stale ({age}s) — skipping", err=True)
            sys.exit(0)

    result = run_severe_once(
        cfg,
        lat=lat,
        lon=lon,
        location_label=cfg.location.name,
        mm=mm,
        dry_run=dry_run,
    )
    if result.skipped_reason:
        click.echo(f"skipped: {result.skipped_reason}")
        return
    click.echo(f"sent {len(result.sent)} message(s); suppressed {len(result.suppressed)}")
    for m in result.sent:
        click.echo(f"  SENT: {m}")
    for s in result.suppressed:
        click.echo(f"  SUPPRESSED: {s}")


@cli.command("test-format")
@click.pass_context
def test_format_cmd(ctx: click.Context) -> None:
    """Fetch + format a conditions message but DO NOT send."""
    cfg = ctx.obj["config"]
    result = post_current_conditions(cfg, dry_run=True)
    if not result.success:
        click.echo(f"failed: {result.reason}", err=True)
        sys.exit(1)


@cli.command("doctor")
@click.pass_context
def doctor_cmd(ctx: click.Context) -> None:
    """Sanity-check config: Tempest reachable, MeshMonitor reachable, send a test ping."""
    cfg = ctx.obj["config"]
    problems: list[str] = []

    click.echo(click.style("=== tempest-mesh doctor ===", bold=True))
    click.echo(f"Config source: {cfg.tempest.source}")
    click.echo(f"MeshMonitor:   {cfg.meshmonitor.url}")
    click.echo(f"Location:      {cfg.location.strategy} ({cfg.location.name})")
    click.echo()

    # Tempest
    click.echo("[1/3] Probing Tempest source...")
    try:
        obs = TempestClient(cfg).fetch()
        click.echo(
            click.style(
                f"  OK — temperature={obs.temperature_f}°F humidity={obs.humidity}%",
                fg="green",
            )
        )
    except TempestFetchError as e:
        click.echo(click.style(f"  FAIL — {e}", fg="red"))
        problems.append(f"tempest: {e}")
    except Exception as e:  # pragma: no cover — network failure paths
        click.echo(click.style(f"  FAIL — unexpected error: {e}", fg="red"))
        problems.append(f"tempest: {e}")

    # MeshMonitor nodes
    click.echo("[2/3] Probing MeshMonitor /api/v1/nodes...")
    mm = MeshMonitorClient(cfg)
    try:
        nodes = mm.list_nodes()
        click.echo(click.style(f"  OK — {len(nodes)} node(s) visible", fg="green"))
        for n in nodes[:10]:
            click.echo(
                f"    - {n.node_id}  lat={n.lat}  lon={n.lon}  role={n.role}  hops={n.hops_away}"
            )
    except Exception as e:
        click.echo(click.style(f"  FAIL — {e}", fg="red"))
        problems.append(f"meshmonitor nodes: {e}")

    # Test post
    click.echo("[3/3] Posting a one-line test broadcast...")
    test_msg = "TEST: tempest-mesh doctor — please ignore"
    try:
        ok = mm.post_message(test_msg, label="doctor-test")
        if ok:
            click.echo(click.style("  OK — test broadcast accepted", fg="green"))
        else:
            click.echo(click.style("  FAIL — MeshMonitor refused the post", fg="red"))
            problems.append("meshmonitor post returned failure")
    except Exception as e:
        click.echo(click.style(f"  FAIL — {e}", fg="red"))
        problems.append(f"meshmonitor post: {e}")

    click.echo()
    if problems:
        click.echo(click.style(f"{len(problems)} problem(s) found:", fg="red", bold=True))
        for p in problems:
            click.echo(f"  - {p}")
        sys.exit(1)
    click.echo(click.style("all checks passed", fg="green", bold=True))


@cli.command("install-systemd")
@click.option(
    "--prefix",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("/etc/systemd/system"),
    help="Where to copy the unit files.",
)
@click.option(
    "--user",
    default="tempestmesh",
    help="User the units will run as.",
)
@click.option(
    "--working-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path.cwd(),
    help="WorkingDirectory for the units (where config.yaml lives).",
)
@click.option("--no-enable", is_flag=True, help="Copy but don't enable/start.")
def install_systemd_cmd(prefix: Path, user: str, working_dir: Path, no_enable: bool) -> None:
    """Render + install systemd units. Requires root (or sudo)."""
    src_dir = Path(__file__).resolve().parent.parent.parent / "systemd"
    if not src_dir.is_dir():
        # When installed via pip, look in the installed package datadir instead.
        click.echo(f"systemd templates dir not found at {src_dir}", err=True)
        sys.exit(2)

    binary = shutil.which("tempest-mesh") or sys.executable + " -m tempest_mesh"

    units = [
        "tempest-mesh-conditions.service",
        "tempest-mesh-conditions.timer",
        "tempest-mesh-severe.service",
        "tempest-mesh-severe.timer",
    ]
    for unit in units:
        src = src_dir / unit
        if not src.exists():
            click.echo(f"missing template {src}", err=True)
            sys.exit(2)
        rendered = src.read_text().format(
            user=user,
            working_dir=str(working_dir),
            binary=binary,
        )
        dst = prefix / unit
        dst.write_text(rendered)
        click.echo(f"wrote {dst}")

    if no_enable:
        return
    for action, unit in [
        ("daemon-reload", None),
        ("enable", "tempest-mesh-conditions.timer"),
        ("enable", "tempest-mesh-severe.timer"),
        ("start", "tempest-mesh-conditions.timer"),
        ("start", "tempest-mesh-severe.timer"),
    ]:
        cmd = ["systemctl", action]
        if unit:
            cmd.append(unit)
        click.echo("$ " + " ".join(cmd))
        subprocess.run(cmd, check=False)


# Allow ``python -m tempest_mesh``.
def main() -> None:  # pragma: no cover
    cli(obj={})


if __name__ == "__main__":  # pragma: no cover
    main()


# Used by the location resolver in tests
__all__ = ["cli", "main", "resolve_location"]
