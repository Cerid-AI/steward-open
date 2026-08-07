# SPDX-License-Identifier: Apache-2.0

"""``steward plans`` — backlog list/show/register/refresh/prune (ADR-0019)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from steward.core.plans.model import plan_record_to_dict
from steward.infra.plans import (
    list_plans,
    prune_plans,
    refresh_plan_status,
    register_plan_from_manifest,
    show_plan,
)

app = typer.Typer(
    name="plans",
    help="Plan backlog browser (data-dir registry; not inventory.db).",
    no_args_is_help=True,
)
console = Console()


def _data_dir_opt() -> Path | None:
    from steward.infra.db.settings import data_dir

    return data_dir()


@app.command("list")
def list_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON array."),
    status: str | None = typer.Option(None, "--status", help="Filter by status."),
    policy: str | None = typer.Option(None, "--policy", help="Filter by policy name."),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
) -> None:
    """List registered plans (newest first)."""
    records = list_plans(status=status, policy=policy, limit=limit)
    if json_out:
        payload = [plan_record_to_dict(r) for r in records]
        console.print_json(json.dumps(payload))
        return
    if not records:
        console.print("[dim]no plans in backlog[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("plan_id", style="cyan", max_width=36)
    table.add_column("status")
    table.add_column("policy")
    table.add_column("rows", justify="right")
    table.add_column("bytes", justify="right")
    table.add_column("blocked")
    table.add_column("created")
    for r in records:
        blocked = ",".join(r.blocked_reasons) if r.blocked_reasons else "—"
        table.add_row(
            r.plan_id[:36],
            r.status,
            r.policy.name,
            f"{r.rows_total:,}",
            f"{r.estimated_bytes:,}",
            blocked,
            r.created_at,
        )
    console.print(table)


@app.command("show")
def show_cmd(
    plan_id: str = typer.Argument(..., help="Plan id (manifest_run_id)."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show one backlog plan record."""
    rec = show_plan(plan_id)
    if rec is None:
        console.print(f"[red]plan not found:[/red] {plan_id}")
        raise typer.Exit(1)
    if json_out:
        console.print_json(json.dumps(plan_record_to_dict(rec)))
        return
    console.print(f"[bold]plan_id[/bold]     {rec.plan_id}")
    console.print(f"status       {rec.status}")
    console.print(f"policy       {rec.policy.name} ({rec.policy.kind})")
    console.print(f"created_at   {rec.created_at}")
    console.print(f"rows         {rec.rows_total:,}")
    console.print(f"bytes        {rec.estimated_bytes:,}")
    console.print(f"actions      {rec.action_counts}")
    console.print(f"blocked      {list(rec.blocked_reasons) or '—'}")
    console.print(f"manifest     {rec.manifest_path}")
    if rec.dry_run is not None:
        console.print(f"dry_run      ok={rec.dry_run.ok} errors={rec.dry_run.errors}")


@app.command("register")
def register_cmd(
    manifest: Path = typer.Option(..., "--manifest", help="Path to plan TSV."),
    policy: str | None = typer.Option(None, "--policy", help="Policy name label."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Register an external/hand-edited TSV into the backlog."""
    if not manifest.is_file():
        console.print(f"[red]manifest not found:[/red] {manifest}")
        raise typer.Exit(2)
    try:
        rec = register_plan_from_manifest(
            manifest,
            policy_name=policy,
            policy_path=policy,
        )
    except Exception as exc:
        console.print(f"[red]register failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    if json_out:
        console.print_json(json.dumps(plan_record_to_dict(rec)))
        return
    console.print(f"[green]✓[/green] registered plan_id={rec.plan_id}")
    console.print(f"  status={rec.status} rows={rec.rows_total:,} bytes={rec.estimated_bytes:,}")
    if rec.blocked_reasons:
        console.print(f"  blocked={list(rec.blocked_reasons)}")


@app.command("refresh")
def refresh_cmd(
    plan_id: str = typer.Argument(..., help="Plan id."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Recompute status from audit + dry_run sidecar (no tier mutation)."""
    try:
        rec = refresh_plan_status(plan_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if json_out:
        console.print_json(json.dumps(plan_record_to_dict(rec)))
        return
    console.print(f"[green]✓[/green] refreshed {rec.plan_id} → status={rec.status}")
    if rec.blocked_reasons:
        console.print(f"  blocked={list(rec.blocked_reasons)}")


@app.command("prune")
def prune_cmd(
    older_than_days: int = typer.Option(90, "--older-than-days"),
    execute: bool = typer.Option(False, "--execute", help="Actually remove plan dirs."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be removed."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Prune old plan artefacts under the data dir (not tier FS)."""
    if not execute and not dry_run:
        console.print("[yellow]specify --dry-run or --execute (ADR-0002)[/yellow]")
        raise typer.Exit(2)
    try:
        result: dict[str, Any] = prune_plans(
            older_than_days=older_than_days,
            execute=execute,
            dry_run=dry_run or not execute,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    if json_out:
        console.print_json(json.dumps(result))
        return
    if execute:
        console.print(f"[green]✓[/green] removed {result['count']} plan(s)")
        for pid in result.get("removed") or []:
            console.print(f"  - {pid}")
    else:
        console.print(f"[yellow]would remove[/yellow] {result['count']} plan(s)")
        for pid in result.get("would_remove") or []:
            console.print(f"  - {pid}")


@app.command("filter-dual-presence")
def filter_dual_presence_cmd(
    manifest: Path = typer.Option(..., "--manifest", help="Plan TSV path."),
    out_dir: Path = typer.Option(..., "--out-dir", help="Write plan-*.tsv + filter-stats.json."),
    store_root: Path | None = typer.Option(None, "--store-root"),
    mount_root: Path | None = typer.Option(None, "--mount-root"),
    limit: int = typer.Option(0, "--limit", min=0, help="Max data rows (0=all)."),
    path_col: str = typer.Option("source_path", "--path-col"),
    intent: str = typer.Option(
        "cloud_retire",
        "--intent",
        help="cloud_retire|local_reclaim|observe (stats intent label only).",
    ),
    register_with: str | None = typer.Option(
        None,
        "--register-with",
        help="Plan backlog id to attach filter-stats.json (clears dual_presence_unfiltered).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Bucket a plan TSV by store/mount dual-presence (ADR-0020).

    Writes child plan artefacts only — never rewrites claims or unlinks files.
    """
    from steward.core.dual_presence import DualPresenceIntent
    from steward.infra.dual_presence import (
        attach_filter_to_plan,
        dual_presence_stats_to_dict,
        filter_plan_file,
        write_filtered_plans,
    )

    if not manifest.is_file():
        console.print(f"[red]manifest not found:[/red] {manifest}")
        raise typer.Exit(2)
    intent_norm = intent.strip().lower()
    if intent_norm not in ("cloud_retire", "local_reclaim", "observe"):
        console.print(f"[red]unknown intent:[/red] {intent}")
        raise typer.Exit(2)
    intent_t: DualPresenceIntent = intent_norm  # type: ignore[assignment]
    try:
        result = filter_plan_file(
            manifest,
            store_root=store_root,
            mount_root=mount_root,
            limit=limit,
            path_col=path_col,
            intent=intent_t,
        )
    except Exception as exc:
        console.print(f"[red]filter failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    if result.stats.counted == 0:
        console.print("[yellow]no data rows[/yellow]")
        raise typer.Exit(1)
    if path_col not in result.fieldnames and result.fieldnames:
        console.print(
            f"[red]missing column {path_col!r}; have {list(result.fieldnames)}[/red]"
        )
        raise typer.Exit(2)

    artifacts = write_filtered_plans(result, out_dir=out_dir)
    attached: str | None = None
    if register_with:
        dest = attach_filter_to_plan(register_with, artifacts)
        attached = str(dest) if dest is not None else None
        if dest is None:
            console.print(
                f"[yellow]could not attach filter to plan {register_with} "
                f"(missing backlog dir?)[/yellow]"
            )

    payload = dual_presence_stats_to_dict(result.stats)
    payload["out_dir"] = artifacts.out_dir
    payload["stats_path"] = artifacts.stats_path
    payload["bucket_paths"] = artifacts.bucket_paths
    if attached:
        payload["attached_filter_stats"] = attached
    if json_out:
        console.print_json(json.dumps(payload))
        return
    console.print(f"[green]✓[/green] wrote filter under {artifacts.out_dir}")
    console.print(
        f"  dual={result.stats.dual} store_only={result.stats.store_only} "
        f"conflict={result.stats.conflict_name_path} mount_error={result.stats.mount_error} "
        f"counted={result.stats.counted}"
    )
    if result.stats.dual:
        console.print(
            f"  cloud candidate: {artifacts.bucket_paths.get('dual', 'plan-dual.tsv')}"
        )
    if attached:
        console.print(f"  attached to plan {register_with}: {attached}")


@app.command("bulk-retire-prep")
def bulk_retire_prep_cmd(
    manifest: Path = typer.Option(..., "--manifest", help="Source plan TSV."),
    out_dir: Path = typer.Option(..., "--out-dir", help="Write dual/store buckets + stats."),
    store_root: Path | None = typer.Option(None, "--store-root"),
    mount_root: Path | None = typer.Option(None, "--mount-root"),
    limit: int = typer.Option(0, "--limit", min=0, help="Max data rows (0=all)."),
    path_col: str = typer.Option("source_path", "--path-col"),
    dry_run_apply: bool = typer.Option(
        False,
        "--dry-run-apply",
        help="Also run apply --dry-run on plan-dual.tsv (still never executes).",
    ),
    require_fp_healthy: bool = typer.Option(
        True,
        "--require-fp-healthy/--no-require-fp-healthy",
        help="When --dry-run-apply, gate on FP health (default on).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Prepare bulk cloud-retire artefacts (filter + optional dry-run; no execute).

    Pipeline: dual-presence filter → plan-dual.tsv → optional apply dry-run.
    **Execute remains operator-gated** via ``steward apply --execute`` only.
    """
    from steward.infra.bulk_retire_prep import (
        bulk_retire_prep_to_dict,
        prepare_bulk_cloud_retire,
    )

    if not manifest.is_file():
        console.print(f"[red]manifest not found:[/red] {manifest}")
        raise typer.Exit(2)
    try:
        result = prepare_bulk_cloud_retire(
            manifest=manifest,
            out_dir=out_dir,
            store_root=store_root,
            mount_root=mount_root,
            limit=limit,
            path_col=path_col,
            run_apply_dry_run=dry_run_apply,
            require_fp_healthy=require_fp_healthy,
        )
    except Exception as exc:
        console.print(f"[red]prep failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    if json_out:
        console.print_json(json.dumps(bulk_retire_prep_to_dict(result)))
        return

    console.print(f"[green]✓[/green] bulk-retire prep → {result.out_dir}")
    console.print(f"  dual_tsv     = {result.dual_tsv or '—'}")
    console.print(f"  stats        = {result.stats_path}")
    console.print("  execute      = blocked (use apply --execute after review)")
    if result.dry_run_summary:
        color = "green" if result.dry_run_ok else "yellow"
        console.print(f"  [{color}]dry-run apply[/{color}] = {result.dry_run_summary}")
    for note in result.notes:
        console.print(f"[dim]{note}[/dim]")


__all__ = ["app"]
