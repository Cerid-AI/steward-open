# SPDX-License-Identifier: Apache-2.0

"""``steward stash`` subcommand group — list / finalize / restore / verify."""

from __future__ import annotations

import csv
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from steward.infra.db.admin import resolve_machine_id
from steward.infra.db.settings import inventory_db_path
from steward.infra.db.stash_cmd import (
    finalize_stash,
    list_stashes,
    restore_stash,
    verify_stash,
)

app = typer.Typer(name="stash", help="Manage cooling-off stash entries.", no_args_is_help=True)
console = Console()


@app.command("list")
def list_cmd() -> None:
    """List in-flight stash groups (committed but not yet finalized/restored)."""
    groups = list_stashes()
    if not groups:
        console.print("[dim]no in-flight stash entries[/dim]")
        return
    tbl = Table(title="Stash entries")
    for col in ("manifest_run_id", "count", "oldest_age_days", "ready_to_finalize?"):
        tbl.add_column(col)
    for g in groups:
        ready = "yes" if g.oldest_age_days >= 7 else "no (< 7d)"
        tbl.add_row(g.manifest_run_id, str(g.count), f"{g.oldest_age_days:.1f}", ready)
    console.print(tbl)


@app.command("finalize")
def finalize_cmd(
    run_id: str = typer.Option(..., "--run-id", help="manifest_run_id of the stash group to finalize."),
    cooling_off_days: int = typer.Option(7, "--cooling-off-days", help="Minimum age before finalize is allowed."),
    force: bool = typer.Option(False, "--force", help="Bypass the cooling-off window (use cautiously)."),
) -> None:
    """Permanently delete the destination files for one stash group."""
    target = inventory_db_path()
    machine_id = resolve_machine_id(target)
    counts = finalize_stash(
        manifest_run_id=run_id,
        machine_id=machine_id,
        cooling_off_days=cooling_off_days,
        force=force,
    )
    console.print(f"[bold]finalize[/bold] run_id={run_id}")
    console.print(f"  finalized      = {counts['finalized']:,}")
    console.print(f"  skipped_young  = {counts['skipped_young']:,}")
    console.print(f"  errored        = {counts['errored']:,}")
    if counts["errored"]:
        raise typer.Exit(1)


@app.command("restore")
def restore_cmd(
    run_id: str = typer.Option(..., "--run-id", help="manifest_run_id of the stash group to restore."),
) -> None:
    """Move the stash group's files back to their original paths."""
    target = inventory_db_path()
    machine_id = resolve_machine_id(target)
    counts = restore_stash(
        manifest_run_id=run_id,
        machine_id=machine_id,
    )
    console.print(f"[bold]restore[/bold] run_id={run_id}")
    console.print(f"  restored          = {counts['restored']:,}")
    console.print(f"  skipped_occupied  = {counts['skipped_occupied']:,}")
    console.print(f"  errored           = {counts['errored']:,}")
    if counts["errored"]:
        raise typer.Exit(1)


@app.command("verify")
def verify_cmd(
    run_id: str = typer.Option(..., "--run-id", help="manifest_run_id of the stash group to verify."),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Optional TSV report path. Columns: status, source_path, "
        "destination_path, permanode_id, canonical_path, error.",
    ),
    also_exclude: list[str] = typer.Option(
        [],
        "--also-exclude",
        help="Additional path prefix to treat as 'not canonical'. Repeatable. "
        "Use when verifying multiple related stash groups as a unit.",
    ),
) -> None:
    """Verify every in-flight stash entry has a canonical copy elsewhere.

    Reports per-entry status (ok | dst-missing | src-still-present |
    no-canonical-elsewhere | no-permanode | error). Exits 1 if any entry
    is in a non-ok state — the operator should investigate before calling
    ``finalize``.
    """
    target = inventory_db_path()
    if not target.exists():
        console.print(f"[red]inventory.db not found at {target}[/red]")
        raise typer.Exit(2)

    results, counts = verify_stash(
        manifest_run_id=run_id,
        also_exclude=list(also_exclude) or None,
    )

    console.print(f"[bold]verify[/bold] run_id={run_id}")
    if counts["total"] == 0:
        console.print(f"  [dim]no in-flight entries for run_id={run_id}[/dim]")
        return
    console.print(f"  total                  = {counts['total']:,}")
    console.print(f"  [green]ok[/green]                    = {counts['ok']:,}")
    for st in ("dst-missing", "src-still-present", "no-canonical-elsewhere", "no-permanode", "error"):
        n = counts.get(st, 0)
        if n:
            console.print(f"  [red]{st:<22}[/red] = {n:,}")

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="") as f:
            w = csv.writer(f, delimiter="\t", lineterminator="\n")
            w.writerow(["status", "source_path", "destination_path", "permanode_id", "canonical_path", "error"])
            for r in results:
                w.writerow(
                    [
                        r.status,
                        r.source_path,
                        r.destination_path,
                        r.permanode_id or "",
                        r.canonical_path or "",
                        r.error or "",
                    ]
                )
        console.print(f"  report: {out}")

    n_bad = counts["total"] - counts["ok"]
    if n_bad:
        console.print(f"[red]verdict: BLOCKED — {n_bad} entry/entries in a non-ok state[/red]")
        raise typer.Exit(1)
    console.print("[green]verdict: SAFE TO FINALIZE[/green]")
