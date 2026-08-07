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
        help="Overlay: none|domain|extension|tier|source|presence (presence = bounded FS probe)",
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


@app.command("plan-seed")
def surface_plan_seed(
    prefix: str = typer.Option(..., "--prefix", help="Required path prefix (no whole-inventory seed)."),
    out: Path = typer.Option(..., "--out", help="Output plan TSV path."),
    action: str = typer.Option(
        "observe",
        "--action",
        help="observe (reclassify seed) | retire_direct (requires dual filter for cloud safety).",
    ),
    limit: int = typer.Option(500, "--limit", min=1, max=50000),
    dual_only: bool = typer.Option(
        False,
        "--dual-only",
        help="Keep only dual-present paths (FS probe; recommended for retire_direct).",
    ),
    register: bool = typer.Option(
        False,
        "--register",
        help="Register seed into plan backlog (ADR-0019).",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Export claims under --prefix to a dry plan TSV skeleton (Wave C).

    Does **not** apply or execute. Review the TSV, then
    ``steward apply --manifest … --dry-run`` (and only later ``--execute``).
    """
    from steward.infra.surface_plan_seed import seed_plan_from_prefix

    action_norm = action.strip().lower()
    if action_norm not in ("observe", "retire_direct"):
        console.print("[red]--action must be observe or retire_direct[/red]")
        raise typer.Exit(2)
    if action_norm == "retire_direct" and not dual_only:
        console.print(
            "[yellow]warn[/yellow]: retire_direct without --dual-only; "
            "prefer --dual-only for cloud-safe candidates"
        )

    target = _ensure_db_ready()
    try:
        result = seed_plan_from_prefix(
            db_path=target,
            path_prefix=prefix,
            out=out,
            action=action_norm,  # type: ignore[arg-type]
            limit=limit,
            dual_presence_only=dual_only,
            register=register,
        )
    except (ValueError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    if json_output:
        print(
            json.dumps(
                {
                    "path": str(result.path),
                    "plan_id": result.plan_id,
                    "rows": result.rows,
                    "action": result.action,
                    "dual_filtered": result.dual_filtered,
                    "notes": list(result.notes),
                },
                default=str,
            )
        )
        return

    console.print(f"[green]✓[/green] wrote plan seed → {result.path}")
    console.print(f"  plan_id = {result.plan_id}")
    console.print(f"  rows    = {result.rows:,}  action={result.action}")
    for note in result.notes:
        console.print(f"[dim]{note}[/dim]")


__all__ = ["app"]
