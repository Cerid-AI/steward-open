# SPDX-License-Identifier: Apache-2.0

"""``steward dashboard`` — single-page HTML status dashboard.

Serves an HTML page that renders the same data ``steward status``
prints to the terminal. Page auto-refreshes every 30 seconds (or
custom interval).

Read-only. Per ADR-0009 (pull-don't-push) the dashboard never mutates;
mutations stay in CLI / MCP-write paths where the operator's
confirmation is structural.

Bind defaults to ``127.0.0.1`` so the page is never reachable from
the network without explicit opt-in (``--host 0.0.0.0``).
"""
from __future__ import annotations

import webbrowser

import typer
from rich.console import Console

from steward.infra.dashboard.server import run_dashboard
from steward.infra.db.admin import migrate
from steward.infra.db.settings import inventory_db_path

app = typer.Typer(
    name="dashboard",
    help="Serve a single-page HTML status dashboard (read-only).",
    invoke_without_command=True,
)
console = Console()


@app.callback(invoke_without_command=True)
def dashboard_cmd(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Bind address. Default loopback-only; pass 0.0.0.0 to expose to LAN.",
    ),
    port: int = typer.Option(
        8080, "--port", help="TCP port to bind."
    ),
    refresh_seconds: int = typer.Option(
        30,
        "--refresh-seconds",
        min=0,
        help="Auto-refresh interval (0 disables).",
    ),
    open_browser: bool = typer.Option(
        False,
        "--open",
        help="Open the dashboard URL in the default browser on start.",
    ),
    quick: bool = typer.Option(
        True,
        "--quick/--full",
        help="Quick status collection by default (skip full audit chain). "
        "Use --full or ?full=1 for complete walks. Recommended for multi-GB DBs.",
    ),
) -> None:
    """Serve the dashboard until interrupted."""
    target = inventory_db_path()
    if not target.exists():
        console.print(
            f"[yellow]inventory.db missing at {target} — running migrate first[/yellow]"
        )
        migrate(target)

    url = f"http://{host}:{port}/"
    console.print(f"[green]✓[/green] dashboard listening on {url}")
    mode = "quick" if quick else "full"
    console.print(
        f"  read-only ({mode}) — JSON at "
        f"http://{host}:{port}/status.json · Ctrl-C to stop"
    )

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001 — best-effort
            console.print(
                f"[dim]could not open browser: {exc}[/dim]"
            )

    try:
        run_dashboard(
            db_path=target,
            host=host,
            port=port,
            refresh_seconds=refresh_seconds,
            quick=quick,
        )
    except KeyboardInterrupt:
        console.print("[yellow]stopping dashboard…[/yellow]")


__all__ = ["app"]
