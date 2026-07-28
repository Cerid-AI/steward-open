# SPDX-License-Identifier: Apache-2.0

"""``steward replicate`` — rclone-backed replication.

Two subcommands:

* ``run`` — execute a :class:`ReplicationPolicy`. Requires explicit
  ``--dry-run`` or ``--execute`` (default rejects, per ADR-0002).
* ``show`` — render the resolved policy + bundled default. Useful for
  operators verifying the YAML before a real run.

The policy can be specified either as a bare filename (resolved against
``src/steward/policies/``) or as a full path.
"""
from __future__ import annotations

import typer
from rich.console import Console

from steward.infra.db.admin import migrate, resolve_machine_id
from steward.infra.db.settings import inventory_db_path
from steward.infra.replicate.orchestrate import resolve_policy_path, run_replicate
from steward.infra.replicate.rclone import RcloneNotInstalledError

app = typer.Typer(
    name="replicate",
    help="rclone-backed replication of tiers + inventory.db.",
    invoke_without_command=True,
    no_args_is_help=True,
)
console = Console()


@app.command("run")
def run_cmd(
    policy: str = typer.Option(
        "replication.yml",
        "--policy",
        help="Policy filename (resolved against bundled policies) "
        "or full path. Default: replication.yml (bundled).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Pass --dry-run through to rclone — neither side mutates.",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Actually move bytes. Required to mutate destination.",
    ),
) -> None:
    """Execute a ReplicationPolicy. Exactly one of --dry-run / --execute
    is required (default rejects, per ADR-0002)."""
    if dry_run == execute:
        console.print(
            "[red]exactly one of --dry-run or --execute is required[/red]"
        )
        raise typer.Exit(2)

    try:
        policy_path = resolve_policy_path(policy)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    target = inventory_db_path()
    if not target.exists():
        console.print(
            f"[yellow]inventory.db missing at {target} — running migrate first[/yellow]"
        )
        migrate(target)
    machine_id = resolve_machine_id(target)

    try:
        report = run_replicate(
            db_path=target,
            policy_path=policy_path,
            machine_id=machine_id,
            dry_run=dry_run,
        )
    except RcloneNotInstalledError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    mode_label = "dry-run" if dry_run else "execute"
    console.print(
        f"[green]✓[/green] replicate ({mode_label}) — policy={policy_path.name}"
    )
    console.print(f"  runs               = {report.runs}")
    console.print(f"  successes          = {report.successes}")
    if report.failures:
        console.print(f"  [red]failures[/red]           = {report.failures}")
    if report.skipped:
        console.print(f"  skipped (disabled) = {report.skipped}")
    console.print(f"  bytes_transferred  = {report.bytes_transferred:,}")
    for sr in report.sources:
        if sr.skipped:
            console.print(f"  [dim]· {sr.name} — disabled in policy[/dim]")
            continue
        assert sr.result is not None  # report shape invariant
        marker = "[green]✓[/green]" if sr.result.returncode == 0 else "[red]✗[/red]"
        console.print(
            f"  {marker} {sr.name}: rc={sr.result.returncode} "
            f"duration={sr.result.duration_seconds:.1f}s "
            f"bytes={int(sr.result.stats.get('bytes', 0)):,}"
        )

    # Non-zero exit if any source failed — CI / cron pipelines need that signal.
    if report.failures:
        raise typer.Exit(1)


@app.command("show")
def show_cmd(
    policy: str = typer.Option(
        "replication.yml",
        "--policy",
        help="Policy filename or path.",
    ),
) -> None:
    """Print the resolved replication policy YAML."""
    try:
        policy_path = resolve_policy_path(policy)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(f"[dim]# {policy_path}[/dim]")
    console.print(policy_path.read_text(encoding="utf-8"))


__all__ = ["app"]
