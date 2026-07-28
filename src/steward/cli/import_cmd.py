# SPDX-License-Identifier: Apache-2.0

"""``steward import`` subcommand group — legacy DB ingest."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from steward.infra.db.admin import migrate, resolve_machine_id
from steward.infra.db.settings import inventory_db_path
from steward.infra.importer.legacy_unified import import_legacy

app = typer.Typer(name="import", help="Ingest legacy data sources (sprawl-audit unified-hash.db, …).", no_args_is_help=True)
console = Console()


@app.command("legacy")
def legacy_cmd(
    source: Path = typer.Option(..., "--source", help="Path to sprawl-audit unified-hash.db"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Walk the source rows but commit nothing"),
    limit: int | None = typer.Option(None, "--limit", help="Only import the first N source rows (debug/test)"),
) -> None:
    """Import sprawl-audit ``unified-hash.db`` into Steward's inventory.

    Idempotent on dry-run; safe to re-run with the same source repeatedly
    (the legacy_import_log captures every invocation, the audit_log chains
    them, and duplicate path/container/scan_run rows are skipped).
    """
    target = inventory_db_path()
    if not target.exists():
        console.print(f"[yellow]inventory.db not found at {target} — running migrate first[/yellow]")
        migrate(target)

    machine_id = resolve_machine_id(target)
    console.print(f"Importing from [bold]{source}[/bold] → [bold]{target}[/bold]")
    console.print(f"  machine_id = {machine_id}    dry_run = {dry_run}    limit = {limit or 'all'}")

    summary = import_legacy(
        source_db=source,
        target_db=target,
        machine_id=machine_id,
        dry_run=dry_run,
        limit=limit,
    )

    console.print("")
    console.print(f"  rows_read              = {summary.rows_read:,}")
    console.print(f"  rows_inserted          = {summary.rows_inserted:,}")
    console.print(f"  rows_skipped (noise)   = {summary.rows_noise_filtered:,}")
    console.print(f"  rows_error_in_source   = {summary.rows_error_in_source:,}")
    console.print(f"  permanodes_unique      = {summary.permanodes_unique:,}")
    console.print(f"  source_db_sha256       = {summary.source_sha256[:16]}…")
    verb = "[yellow]dry-run — nothing committed[/yellow]" if dry_run else "[green]✓ committed[/green]"
    console.print(f"  {verb}")
