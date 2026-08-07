# SPDX-License-Identifier: Apache-2.0
"""``steward surface`` — inventory path tree / surface exploration (ADR-0022)."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from steward.infra.db.admin import migrate
from steward.infra.db.settings import inventory_db_path
from steward.infra.status import _format_bytes

app = typer.Typer(
    name="surface",
    help="Inventory surface: path-tree exploration (ADR-0022).",
    no_args_is_help=True,
)
console = Console()


def _ensure_db_ready() -> Path:
    target = inventory_db_path()
    if not target.exists():
        console.print(f"[yellow]inventory.db missing at {target} — running migrate first[/yellow]")
        migrate(target)
    return target


@app.command("tree")
def surface_tree(
    prefix: str = typer.Option("", "--prefix", help="Path prefix to drill (empty = roots)"),
    color_by: str = typer.Option(
        "none",
        "--color-by",
        help="Overlay: none|domain|extension|tier|source",
    ),
    tier: str | None = typer.Option(None, "--tier"),
    volume: str | None = typer.Option(None, "--volume"),
    limit: int = typer.Option(100, "--limit", min=1),
    include_imports: bool = typer.Option(False, "--include-imports"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Depth-1 children under --prefix sized by bytes (inventory claims)."""
    from steward.core.matrix.types import PathTreeRequest
    from steward.core.matrix.validate import MatrixValidationError
    from steward.infra.stats_tree import path_tree_depth1, path_tree_to_dict

    target = _ensure_db_ready()
    try:
        req = PathTreeRequest(
            path_prefix=prefix,
            color_by=color_by,  # type: ignore[arg-type]
            child_limit=limit,
            include_imports=include_imports,
            tier=tier,
            volume=volume,
        )
        res = path_tree_depth1(db_path=target, req=req)
    except (MatrixValidationError, TypeError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    if json_output:
        print(json.dumps(path_tree_to_dict(res), default=str))
        return

    title = f"surface tree @ {res.path_prefix or '(root)'}"
    t = Table(show_header=True, title=title, title_justify="left")
    t.add_column("name")
    t.add_column("kind")
    t.add_column("claims", justify="right")
    t.add_column("bytes", justify="right")
    if res.color_by != "none":
        t.add_column("overlay")
    for c in res.children:
        row = [
            c.name,
            "dir" if c.is_dir else "file",
            f"{c.claim_count:,}",
            _format_bytes(c.total_bytes),
        ]
        if res.color_by != "none":
            row.append(c.overlay_value or "—")
        t.add_row(*row)
    console.print(t)
    if res.truncated:
        console.print("[yellow]truncated[/yellow] — narrow with --prefix/--tier/--volume or raise --limit")
    for note in res.notes:
        console.print(f"[dim]{note}[/dim]")


__all__ = ["app"]
