# SPDX-License-Identifier: Apache-2.0

"""Steward CLI root — ``steward`` console-script entry point.

Core subcommands are always registered. Lab/macOS-heavy adapters
(``photos``, ``schedule``) register only when their modules import
successfully so the open-core extract can omit those packages.
"""

from __future__ import annotations

import importlib
import logging

import typer

from steward._version import __version__
from steward.cli import (
    apply_cmd,
    archive_cmd,
    classify_cmd,
    dashboard_cmd,
    db_cmd,
    embed_cmd,
    fp_cmd,
    import_cmd,
    inspect_cmd,
    machines_cmd,
    mcp_cmd,
    policy_cmd,
    replicate_cmd,
    scan_cmd,
    search_cmd,
    stash_cmd,
    stats_cmd,
    status_cmd,
    watch_cmd,
)

_log = logging.getLogger("steward.cli.main")

app = typer.Typer(
    name="steward",
    help="Filesystem stewardship: scan, classify, plan, apply.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(db_cmd.app, name="db")
app.add_typer(import_cmd.app, name="import")
app.add_typer(scan_cmd.app, name="scan")
app.add_typer(policy_cmd.app, name="policy")
app.add_typer(inspect_cmd.app, name="inspect")
app.add_typer(apply_cmd.app, name="apply")
app.add_typer(stash_cmd.app, name="stash")
app.add_typer(classify_cmd.app, name="classify")
app.add_typer(watch_cmd.app, name="watch")
app.add_typer(embed_cmd.app, name="embed")
app.add_typer(search_cmd.app, name="search")
app.add_typer(mcp_cmd.app, name="mcp")
app.add_typer(replicate_cmd.app, name="replicate")
app.add_typer(archive_cmd.app, name="archive")
app.add_typer(status_cmd.app, name="status")
app.add_typer(machines_cmd.app, name="machines")
app.add_typer(dashboard_cmd.app, name="dashboard")
app.add_typer(stats_cmd.app, name="stats")
app.add_typer(fp_cmd.app, name="fp")


def _try_add_optional(module_path: str, name: str) -> None:
    """Register optional CLI groups when the module is present."""
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        _log.debug("optional CLI %s unavailable: %s", name, exc)
        return
    app.add_typer(mod.app, name=name)


_try_add_optional("steward.cli.photos_cmd", "photos")
_try_add_optional("steward.cli.schedule_cmd", "schedule")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"steward {__version__}")
        raise typer.Exit(0)


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print Steward version and exit.",
    ),
) -> None:
    """Filesystem stewardship CLI."""


__all__ = ["app"]
