# SPDX-License-Identifier: Apache-2.0

"""``steward fp`` — Cloud File Provider / Dropbox probes."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from steward.infra.fp_status import collect_fp_status, fp_status_to_dict

app = typer.Typer(
    name="fp",
    help="Cloud File Provider probes (Dropbox store vs mount).",
)
console = Console()


@app.command("status")
def fp_status(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Dropbox layout + dual-presence + structured health verdict."""
    report = collect_fp_status()
    if json_output:
        print(json.dumps(fp_status_to_dict(report), indent=2))
        return

    t = Table(show_header=False, title="cloud-FP roots", title_justify="left")
    t.add_column("k")
    t.add_column("v")
    t.add_row("mount", report.mount_root)
    t.add_row(
        "mount exists",
        f"{report.mount.exists}  dev={report.mount.st_dev}"
        + (f"  err={report.mount.error}" if report.mount.error else ""),
    )
    t.add_row("store", report.store_root)
    t.add_row(
        "store exists",
        f"{report.store.exists}  dev={report.store.st_dev}"
        + (f"  err={report.store.error}" if report.store.error else ""),
    )
    fork_label = (
        "[yellow]yes (external-drive OK)[/yellow]"
        if report.forked_devices and report.verdict and report.verdict.layout == "external_drive_fp"
        else ("[red]yes[/red]" if report.forked_devices else "[green]no[/green]")
    )
    t.add_row("forked_devices", fork_label)
    if report.dropbox_info_path:
        t.add_row("dropbox info.json path", report.dropbox_info_path)
    if report.domain is not None:
        d = report.domain
        if d.error:
            t.add_row("fp domain", f"[yellow]error[/yellow] {d.error}")
        else:
            if d.is_fpfs_placeholder or d.reports_disconnected:
                state = "[yellow]residual/disconnected metadata[/yellow]"
            elif d.connected:
                state = "[green]connected[/green]"
            else:
                state = "unknown"
            t.add_row("fp domain", state)
            if d.domain_id:
                t.add_row("domain id", d.domain_id)
            if d.disconnection_reason:
                t.add_row("disconnect reason", d.disconnection_reason)
            if d.domain_path:
                t.add_row("domain Path", d.domain_path)
            if d.supports_syncing_trash is not None:
                t.add_row(
                    "SupportsSyncingTrash",
                    str(d.supports_syncing_trash),
                )
    if report.verdict is not None:
        v = report.verdict
        t.add_row("layout", v.layout)
        t.add_row(
            "cloud_retire_ready",
            ("[green]yes[/green]" if v.cloud_retire_ready else "[red]no[/red]"),
        )
        t.add_row(
            "local_reclaim_ready",
            ("[green]yes[/green]" if v.local_reclaim_ready else "[red]no[/red]"),
        )
    console.print(t)

    if report.name_divergence is not None:
        nd = report.name_divergence
        if nd.error:
            console.print(f"[yellow]name divergence:[/yellow] {nd.error}")
        elif nd.store_only or nd.mount_only:
            ntable = Table(title="top-level name divergence", title_justify="left")
            ntable.add_column("side")
            ntable.add_column("basenames")
            if nd.store_only:
                ntable.add_row("store_only", ", ".join(nd.store_only[:12]))
            if nd.mount_only:
                ntable.add_row("mount_only", ", ".join(nd.mount_only[:12]))
            ntable.add_row("both_count", str(nd.both_count))
            console.print(ntable)

    s = Table(title="sample dual-presence", title_justify="left")
    s.add_column("relative")
    s.add_column("store")
    s.add_column("mount")
    s.add_column("size_match")
    for sample in report.dual_samples:
        s.add_row(
            sample.relative,
            "yes" if sample.store_exists else "no",
            "yes" if sample.mount_exists else "no",
            (
                "—"
                if sample.size_match is None
                else ("yes" if sample.size_match else "NO")
            ),
        )
    console.print(s)
    console.print(
        f"summary: both={report.sample_both} store_only={report.sample_store_only} "
        f"mount_only={report.sample_mount_only} neither={report.sample_neither}"
    )

    if report.verdict and report.verdict.problems:
        console.print("[bold red]problems (block --require-fp-healthy)[/bold red]")
        for p in report.verdict.problems:
            console.print(f"  • {p}")
    if report.verdict and report.verdict.warnings:
        console.print("[bold yellow]warnings[/bold yellow]")
        for w in report.verdict.warnings:
            console.print(f"  • {w}")
    console.print("[bold]recommendations[/bold]")
    for r in report.recommendations:
        console.print(f"  • {r}")
    for n in report.notes:
        console.print(f"  [dim]{n}[/dim]")


@app.command("dual-presence")
def fp_dual_presence(
    sample: int = typer.Option(32, "--sample", min=1, max=10_000, help="Max paths to probe."),
    db: Path | None = typer.Option(None, "--db", help="Inventory DB for claim sample."),
    rels: str | None = typer.Option(
        None,
        "--rels",
        help="Comma-separated fixed relatives (fp_status style; ignores --db).",
    ),
    store_root: Path | None = typer.Option(None, "--store-root"),
    mount_root: Path | None = typer.Option(None, "--mount-root"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Bounded dual-presence sample (ADR-0020). Observe only; no FS mutation."""
    from steward.infra.dual_presence import (
        collect_stats_from_fixed_rels,
        dual_presence_stats_to_dict,
    )

    if rels:
        rel_list = [r.strip() for r in rels.split(",") if r.strip()]
        stats = collect_stats_from_fixed_rels(
            store_root=store_root,
            mount_root=mount_root,
            rels=rel_list,
            intent="observe",
        )
    elif db is not None:
        from steward.infra.dual_presence import sample_from_inventory

        target = Path(db).expanduser()
        if not target.is_file():
            console.print(f"[red]inventory missing:[/red] {target}")
            raise typer.Exit(2)
        stats = sample_from_inventory(
            target,
            store_root=store_root,
            mount_root=mount_root,
            limit=sample,
            intent="observe",
        )
    else:
        stats = collect_stats_from_fixed_rels(
            store_root=store_root,
            mount_root=mount_root,
            intent="observe",
        )

    payload = dual_presence_stats_to_dict(stats)
    if json_output:
        print(json.dumps(payload, indent=2))
        return
    t = Table(title="dual-presence sample", title_justify="left")
    t.add_column("kind")
    t.add_column("count", justify="right")
    for kind in (
        "dual",
        "store_only",
        "mount_only",
        "missing_store",
        "conflict_name_path",
        "outside_store_root",
        "mount_error",
        "unknown",
    ):
        t.add_row(kind, str(payload.get(kind, 0)))
    console.print(t)
    ratio = payload.get("cloud_safe_sample_ratio")
    console.print(
        f"counted={stats.counted} ratio={ratio!s} "
        f"store={stats.store_root} mount={stats.mount_root}"
    )
    console.print(
        "[dim]For bulk cloud retire hygiene: steward plans filter-dual-presence[/dim]"
    )




__all__ = ["app"]
