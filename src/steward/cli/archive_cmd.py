# SPDX-License-Identifier: Apache-2.0

"""``steward archive`` — restic-backed encrypted archive tier.

Four subcommands:

* ``init`` — create the restic repository (one-time per repo). Requires
  ``--execute``.
* ``snapshot`` — back up enabled sources. Requires ``--dry-run`` or
  ``--execute`` (per ADR-0002).
* ``list`` — read-only: ``restic snapshots`` for each unique repository.
* ``show`` — print the resolved policy YAML.

Restic encryption keys live in the operator's keychain (or password
file). Steward never reads them. The policy YAML supplies a
``password_command`` that restic invokes on demand.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from steward.infra.archive.orchestrate import (
    resolve_policy_path,
    run_init,
    run_list,
    run_snapshot,
)
from steward.infra.archive.restic import ResticNotInstalledError
from steward.infra.db.admin import migrate, resolve_machine_id
from steward.infra.db.settings import inventory_db_path

app = typer.Typer(
    name="archive",
    help="restic-backed encrypted archive tier.",
    invoke_without_command=True,
    no_args_is_help=True,
)
console = Console()


def _resolve_policy_or_exit(name: str) -> Path:
    try:
        return resolve_policy_path(name)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def _ensure_db_ready() -> tuple[Path, str]:
    target = inventory_db_path()
    if not target.exists():
        console.print(f"[yellow]inventory.db missing at {target} — running migrate first[/yellow]")
        migrate(target)
    machine_id = resolve_machine_id(target)
    return target, machine_id


# ─────────────────────── snapshot ──────────────────────────


@app.command("snapshot")
def snapshot_cmd(
    policy: str = typer.Option(
        "archive.yml",
        "--policy",
        help="Policy filename (bundled) or full path. Default: archive.yml.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Pass --dry-run to restic — neither side mutates."),
    execute: bool = typer.Option(False, "--execute", help="Actually create a snapshot."),
) -> None:
    """Snapshot each enabled source into its repository."""
    if dry_run == execute:
        console.print("[red]exactly one of --dry-run or --execute is required[/red]")
        raise typer.Exit(2)

    policy_path = _resolve_policy_or_exit(policy)
    target, machine_id = _ensure_db_ready()

    try:
        report = run_snapshot(
            db_path=target,
            policy_path=policy_path,
            machine_id=machine_id,
            dry_run=dry_run,
        )
    except ResticNotInstalledError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    mode_label = "dry-run" if dry_run else "execute"
    console.print(f"[green]✓[/green] archive snapshot ({mode_label}) — policy={policy_path.name}")
    console.print(f"  runs               = {report.runs}")
    console.print(f"  successes          = {report.successes}")
    if report.failures:
        console.print(f"  [red]failures[/red]           = {report.failures}")
    if report.skipped:
        console.print(f"  skipped (disabled) = {report.skipped}")
    console.print(f"  total_bytes_added  = {report.total_bytes_added:,}")
    for sr in report.sources:
        if sr.skipped:
            console.print(f"  [dim]· {sr.name} — disabled in policy[/dim]")
            continue
        assert sr.result is not None
        marker = "[green]✓[/green]" if sr.result.returncode == 0 else "[red]✗[/red]"
        snap_id = sr.result.summary.get("snapshot_id", "—")
        bytes_added = int(sr.result.summary.get("data_added", 0))
        console.print(
            f"  {marker} {sr.name}: rc={sr.result.returncode} "
            f"duration={sr.result.duration_seconds:.1f}s "
            f"snap={snap_id} data_added={bytes_added:,}"
        )

    if report.failures:
        raise typer.Exit(1)


# ─────────────────────── list ──────────────────────────


@app.command("list")
def list_cmd(
    policy: str = typer.Option(
        "archive.yml",
        "--policy",
        help="Policy filename or full path.",
    ),
) -> None:
    """List snapshots from every unique repository in the policy."""
    policy_path = _resolve_policy_or_exit(policy)
    target, machine_id = _ensure_db_ready()

    try:
        report = run_list(
            db_path=target,
            policy_path=policy_path,
            machine_id=machine_id,
        )
    except ResticNotInstalledError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    if report.failures:
        for f in report.failures:
            console.print(f"[red]✗ {f['repository']}[/red]: rc={f['returncode']}")

    if not report.snapshots:
        console.print("[dim]no snapshots in any listed repository.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("time", width=20)
    table.add_column("repository")
    table.add_column("short_id", width=10)
    table.add_column("paths")
    table.add_column("tags")
    for s in report.snapshots:
        table.add_row(
            str(s.get("time", ""))[:19],
            str(s.get("_repository", "")),
            str(s.get("short_id", ""))[:8],
            ",".join(s.get("paths", [])),
            ",".join(s.get("tags", [])),
        )
    console.print(table)

    if report.failures:
        raise typer.Exit(1)


# ─────────────────────── init ──────────────────────────


@app.command("init")
def init_cmd(
    policy: str = typer.Option("archive.yml", "--policy", help="Policy filename or path."),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Required to actually create the repository. Default rejects (per ADR-0002).",
    ),
) -> None:
    """Initialize a restic repository for each unique repo declared in the policy."""
    if not execute:
        console.print("[red]`steward archive init` requires --execute (creates a new encrypted repository).[/red]")
        raise typer.Exit(2)

    policy_path = _resolve_policy_or_exit(policy)
    target, machine_id = _ensure_db_ready()

    try:
        results = run_init(
            db_path=target,
            policy_path=policy_path,
            machine_id=machine_id,
        )
    except ResticNotInstalledError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    any_failed = False
    for repository, result in results:
        marker = "[green]✓[/green]" if result.returncode == 0 else "[red]✗[/red]"
        console.print(f"  {marker} {repository}: rc={result.returncode}")
        if result.returncode != 0:
            any_failed = True
            console.print(f"    [dim]{result.stderr_tail[-200:]}[/dim]")

    if any_failed:
        raise typer.Exit(1)


# ─────────────────────── show ──────────────────────────


@app.command("show")
def show_cmd(
    policy: str = typer.Option("archive.yml", "--policy", help="Policy filename or path."),
) -> None:
    """Print the resolved archive policy YAML."""
    policy_path = _resolve_policy_or_exit(policy)
    console.print(f"[dim]# {policy_path}[/dim]")
    console.print(policy_path.read_text(encoding="utf-8"))


__all__ = ["app"]
