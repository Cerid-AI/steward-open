# SPDX-License-Identifier: Apache-2.0

"""``steward fp`` — Cloud File Provider / Dropbox probes."""

from __future__ import annotations

import json

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


__all__ = ["app"]
