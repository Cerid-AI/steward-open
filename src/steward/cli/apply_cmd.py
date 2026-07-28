# SPDX-License-Identifier: Apache-2.0

"""``steward apply`` — execute a plan manifest.

Per ADR-0002, ``apply`` without an explicit ``--dry-run`` or ``--execute``
flag exits 2. This is structural, not configurable: the operator-in-the-loop
contract demands an explicit decision before any mutation.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from steward.infra.db.admin import resolve_machine_id
from steward.infra.db.apply import ApplyRefused, apply_manifest
from steward.infra.db.settings import inventory_db_path

app = typer.Typer(name="apply", help="Apply a plan manifest (--dry-run or --execute required).", invoke_without_command=True)
console = Console()


@app.callback(invoke_without_command=True)
def apply_cmd(
    manifest: Path = typer.Option(..., "--manifest", help="Path to the plan manifest TSV."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Walk the manifest, content-verify, write zero data."),
    execute: bool = typer.Option(False, "--execute", help="Apply the manifest (writes to FS + audit_log)."),
    max_files: int | None = typer.Option(None, "--max-files", help="Cap the number of rows applied this invocation."),
    skip_verify: bool = typer.Option(
        False,
        "--skip-verify",
        help="For retire_direct rows ONLY: skip per-file hash + size verify; "
        "trust inventory's recorded hash. Existence check still runs. "
        "Use at FP-tier scale where per-file FP hydration is the bottleneck. "
        "See ADR-0014 for safety trade-offs.",
    ),
    allow_store_path_unlink: bool = typer.Option(
        False,
        "--allow-store-path-unlink",
        help="For retire_direct on Dropbox FP: unlink the claim/store path "
        "instead of the user-facing mount (ADR-0015). Local reclaim only — "
        "cloud trash / quota reclaim are NOT guaranteed.",
    ),
    require_fp_healthy: bool = typer.Option(
        False,
        "--require-fp-healthy",
        help="Refuse apply when the manifest touches Dropbox/cloud-FP rows "
        "and steward fp status reports fork/congestion/missing mount. "
        "Does not perform Dropbox rectification.",
    ),
) -> None:
    if dry_run == execute:  # both true (mutually exclusive) or both false (missing flag)
        if dry_run and execute:
            console.print("[red]✗[/red] --dry-run and --execute are mutually exclusive.")
        else:
            console.print(
                "[red]✗[/red] one of --dry-run or --execute is required. "
                "Operator-in-the-loop contract is structural; no defaulting."
            )
        raise typer.Exit(2)

    if skip_verify:
        console.print(
            "[yellow]⚠[/yellow]  --skip-verify is set. Hash + size verification "
            "is SKIPPED for retire_direct rows. The cooling-off mechanism "
            "(e.g. Dropbox cloud trash) remains the only recovery path. "
            "Use only when inventory hashes are trusted."
        )

    if allow_store_path_unlink:
        console.print(
            "[yellow]⚠[/yellow]  --allow-store-path-unlink is set. Dropbox FP "
            "retires will unlink the claim/store path (local reclaim). Cloud "
            "propagation is NOT guaranteed — see ADR-0015."
        )

    prefer_mount = not allow_store_path_unlink
    if require_fp_healthy:
        from steward.infra.fp_preflight import (
            fp_health_problems,
            manifest_needs_fp_health,
        )

        if manifest_needs_fp_health(manifest):
            problems = fp_health_problems(prefer_mount_unlink=prefer_mount)
            if problems:
                console.print(
                    "[red]✗[/red] --require-fp-healthy: cloud-FP pre-flight failed:"
                )
                for p in problems:
                    console.print(f"  • {p}")
                console.print(
                    "[dim]Run `steward fp status`. Dropbox tree rectification "
                    "is deferred — see docs/OPEN_DEVELOPMENT.md.[/dim]"
                )
                raise typer.Exit(2)

    target = inventory_db_path()
    machine_id = resolve_machine_id(target)
    try:
        result = apply_manifest(
            manifest_path=manifest,
            machine_id=machine_id,
            dry_run=dry_run,
            max_files=max_files,
            skip_verify=skip_verify,
            prefer_mount_unlink=prefer_mount,
        )
    except ApplyRefused as exc:
        rejected = exc.result.rejected_imported_claims
        console.print(
            f"[red]✗[/red] apply rejected by pre-flight: "
            f"{len(rejected)} row(s) reference attached-only permanodes "
            "(ADR-0013)."
        )
        for msg in rejected[:20]:
            console.print(f"  • {msg}")
        if len(rejected) > 20:
            console.print(f"  • … and {len(rejected) - 20} more")
        console.print(
            "[dim]Use `steward db imports list` to see what's attached "
            "and `steward db imports detach` to remove a stale "
            "attachment.[/dim]"
        )
        raise typer.Exit(2) from exc

    verb = "DRY-RUN" if result.dry_run else "EXECUTED"
    console.print(f"[bold]{verb}[/bold]  manifest_run_id = {result.manifest_run_id}")
    console.print(f"  rows_total   = {result.rows_total:,}")
    console.print(f"  rows_applied = {result.rows_applied:,}")
    console.print(f"  rows_skipped = {result.rows_skipped:,}")
    console.print(f"  rows_errored = {result.rows_errored:,}")
    if result.nas_export_path:
        console.print(
            f"  [cyan]nas_manifest export[/cyan] = {result.nas_export_path}\n"
            "  [dim]NAS rows were recorded for DSM/SSH — Steward does not "
            "delete on Backup.[/dim]"
        )
    if result.errors:
        console.print("[yellow]errors:[/yellow]")
        for err in result.errors[:20]:
            console.print(f"  • {err}")
        if len(result.errors) > 20:
            console.print(f"  • … and {len(result.errors) - 20} more")
        raise typer.Exit(1)
