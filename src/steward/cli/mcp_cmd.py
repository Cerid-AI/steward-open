# SPDX-License-Identifier: Apache-2.0

"""``steward mcp`` — run the read-only MCP server.

Two transports:

* ``--transport stdio`` (default) — speaks MCP over stdin/stdout. The
  shape every desktop LLM client expects.
* ``--transport http`` — serves the MCP streamable-HTTP transport on
  ``--host`` / ``--port``. Useful for non-stdio clients.

The server exposes only read-only tools — see
:mod:`steward.infra.mcp.handlers`. There is no write surface at all
(per ADR-0002).
"""
from __future__ import annotations

import typer
from rich.console import Console

from steward.infra.db.admin import migrate
from steward.infra.db.settings import inventory_db_path
from steward.infra.mcp.server import build_server

app = typer.Typer(
    name="mcp",
    help="MCP server exposing Steward's inventory (read tools + write tools with destructive hints).",
    invoke_without_command=True,
)
console = Console()


@app.callback(invoke_without_command=True)
def mcp_cmd(
    transport: str = typer.Option(
        "stdio",
        "--transport",
        help="MCP transport: 'stdio' (default) or 'http' (streamable).",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="HTTP host (ignored when transport=stdio).",
    ),
    port: int = typer.Option(
        8765,
        "--port",
        help="HTTP port (ignored when transport=stdio).",
    ),
) -> None:
    """Run the MCP server until interrupted."""
    target = inventory_db_path()
    if not target.exists():
        console.print(
            f"[yellow]inventory.db missing at {target} — running migrate first[/yellow]"
        )
        migrate(target)

    server = build_server()
    # FastMCP accepts host/port through its own settings; we
    # construct the host/port on the server before run() so HTTP
    # transport picks them up.
    server.settings.host = host
    server.settings.port = port

    if transport == "stdio":
        console.print("[green]steward MCP[/green] listening on stdio")
        server.run(transport="stdio")
    elif transport == "http":
        console.print(
            f"[green]steward MCP[/green] listening on http://{host}:{port}/mcp"
        )
        server.run(transport="streamable-http")
    else:
        console.print(f"[red]unknown transport: {transport}[/red]")
        raise typer.Exit(2)


__all__ = ["app"]
