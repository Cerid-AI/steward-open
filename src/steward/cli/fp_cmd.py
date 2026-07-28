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
    json_output: bool = typer.Option(
        False, "--json", help="Emit JSON on stdout."
    ),
) -> None:
    """Lightweight Dropbox store/mount fork + sample dual-presence probe."""
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
    t.add_row(
        "forked_devices",
        "[red]yes[/red]" if report.forked_devices else "[green]no[/green]",
    )
    console.print(t)

    s = Table(title="sample dual-presence", title_justify="left")
    s.add_column("relative")
    s.add_column("store")
    s.add_column("mount")
    s.add_column("size_match")
    for d in report.dual_samples:
        s.add_row(
            d.relative,
            "yes" if d.store_exists else "no",
            "yes" if d.mount_exists else "no",
            (
                "—"
                if d.size_match is None
                else ("yes" if d.size_match else "NO")
            ),
        )
    console.print(s)
    console.print(
        f"summary: both={report.sample_both} store_only={report.sample_store_only} "
        f"mount_only={report.sample_mount_only} neither={report.sample_neither}"
    )
    console.print("[bold]recommendations[/bold]")
    for r in report.recommendations:
        console.print(f"  • {r}")
    for n in report.notes:
        console.print(f"  [dim]{n}[/dim]")


__all__ = ["app"]
