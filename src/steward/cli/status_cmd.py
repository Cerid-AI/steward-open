# SPDX-License-Identifier: Apache-2.0

"""``steward status`` — single-pane operator dashboard.

Reads the inventory and recent audit-log entries to summarise:

* DB file location, size, last-modified
* Counts: permanodes, current claims, scan_runs, audit_log
* Latest scan_run + counters
* In-flight stash entries
* Latest replicate run + archive run
* Audit-chain integrity

Two output modes:

* default — Rich tables, sections separated by horizontal rules.
* ``--json`` — single JSON object on stdout, suitable for piping into
  ``jq`` or scheduled jobs.

Read-only. Never writes; never invokes external tools.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from steward.infra.db.admin import migrate
from steward.infra.db.settings import inventory_db_path
from steward.infra.status import (
    StatusReport,
    _format_bytes,
    collect_status,
    status_to_dict,
)

app = typer.Typer(
    name="status",
    help="Operator dashboard — counts, latest runs, audit-chain health.",
    invoke_without_command=True,
)
console = Console()


def _render_inventory(report: StatusReport) -> Table:
    t = Table(show_header=False, title="inventory", title_justify="left")
    t.add_column("k")
    t.add_column("v", justify="right")
    t.add_row("permanodes", f"{report.inventory.permanodes:,}")
    t.add_row("current claims", f"{report.inventory.current_claims:,}")
    t.add_row("scan runs", f"{report.inventory.scan_runs:,}")
    t.add_row("audit entries", f"{report.inventory.audit_entries:,}")
    t.add_row("machines", f"{report.inventory.machines:,}")
    t.add_row(
        "db file",
        f"{report.db.path}  ({_format_bytes(report.db.size_bytes)})",
    )
    if report.db.modified_iso:
        t.add_row("db modified", report.db.modified_iso)
    return t


def _render_latest_scan(report: StatusReport) -> Table:
    t = Table(show_header=False, title="latest scan", title_justify="left")
    t.add_column("k")
    t.add_column("v", justify="right")
    s = report.latest_scan
    if s.scan_run_id is None:
        t.add_row("status", "[dim]no scans yet[/dim]")
        return t
    t.add_row("scan_run_id", str(s.scan_run_id))
    t.add_row("root", str(s.root_path))
    t.add_row("finished_at", str(s.finished_at))
    t.add_row("files_walked", f"{s.files_walked:,}")
    t.add_row("files_hashed", f"{s.files_hashed:,}")
    t.add_row("files_skipped", f"{s.files_skipped:,}")
    t.add_row("bytes_hashed", _format_bytes(s.bytes_hashed))
    if s.errors:
        t.add_row("[red]errors[/red]", f"{s.errors:,}")
    return t


def _render_stash(report: StatusReport) -> Table:
    t = Table(show_header=False, title="stash", title_justify="left")
    t.add_column("k")
    t.add_column("v", justify="right")
    s = report.stash
    if s.in_flight_entries == 0:
        t.add_row("status", "[dim]no in-flight stash entries[/dim]")
        return t
    t.add_row("in_flight_entries", f"{s.in_flight_entries:,}")
    t.add_row("distinct_run_ids", f"{s.distinct_run_ids:,}")
    t.add_row("oldest", str(s.oldest_ts_iso))
    t.add_row("newest", str(s.newest_ts_iso))
    return t


def _render_adapter(report: StatusReport, *, attr: str, title: str) -> Table:
    t = Table(show_header=False, title=title, title_justify="left")
    t.add_column("k")
    t.add_column("v", justify="right")
    run = getattr(report, attr)
    if run is None:
        t.add_row("status", f"[dim]no {attr.replace('last_', '')} runs yet[/dim]")
        return t
    t.add_row("timestamp", str(run.timestamp))
    if run.policy_name:
        t.add_row("policy", run.policy_name)
    # Surface the headline fields per adapter.
    if attr == "last_replicate":
        runs = int(run.payload.get("runs", 0))
        successes = int(run.payload.get("successes", 0))
        failures = int(run.payload.get("failures", 0))
        bytes_n = int(run.payload.get("bytes_transferred", 0))
        t.add_row("runs", f"{runs:,}")
        t.add_row("successes", f"{successes:,}")
        if failures:
            t.add_row("[red]failures[/red]", f"{failures:,}")
        t.add_row("bytes_transferred", _format_bytes(bytes_n))
    elif attr == "last_archive":
        runs = int(run.payload.get("runs", 0))
        successes = int(run.payload.get("successes", 0))
        failures = int(run.payload.get("failures", 0))
        bytes_n = int(run.payload.get("total_bytes_added", 0))
        t.add_row("runs", f"{runs:,}")
        t.add_row("successes", f"{successes:,}")
        if failures:
            t.add_row("[red]failures[/red]", f"{failures:,}")
        t.add_row("total_bytes_added", _format_bytes(bytes_n))
    return t


def _render_audit(report: StatusReport) -> Table:
    t = Table(show_header=False, title="audit chain", title_justify="left")
    t.add_column("k")
    t.add_column("v", justify="right")
    a = report.audit_chain
    if a.skipped:
        t.add_row("status", "[dim]skipped (--quick)[/dim]")
        return t
    t.add_row("rows_checked", f"{a.rows_checked:,}")
    if a.ok:
        t.add_row("status", "[green]ok[/green]")
    else:
        t.add_row("status", "[red]BROKEN[/red]")
        if a.error:
            t.add_row("error", a.error)
    return t


@app.callback(invoke_without_command=True)
def status_cmd(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a single JSON object on stdout instead of Rich tables.",
    ),
    quick: bool = typer.Option(
        False,
        "--quick",
        help="Skip full audit-chain walk and heavy stash CTE; prefer "
        "inventory rollups when fresh. For multi-GB inventories.",
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Recompute inventory COUNT rollups into meta (writes).",
    ),
    include_imports: bool = typer.Option(
        False,
        "--include-imports",
        help="Fan-out inventory counts across attached imports (ADR-0013). "
        "Collector already supported this; CLI flag for parity with machines/stats.",
    ),
) -> None:
    """Print a dashboard of inventory health."""
    target = inventory_db_path()
    if not target.exists():
        if json_output:
            print(json.dumps({"error": f"inventory.db not found at {target}"}))
            raise typer.Exit(2)
        console.print(f"[yellow]inventory.db missing at {target} — running migrate first[/yellow]")
        migrate(target)

    report = collect_status(
        db_path=target,
        quick=quick,
        refresh_rollups=refresh,
        include_imports=include_imports,
    )

    # ADR-0017: status --refresh also appends one compact health snapshot
    # (data-dir JSONL series) so rollup refresh stays the operator cadence.
    if refresh:
        from pathlib import Path as _Path

        from steward.infra.observability import log_swallowed_error

        try:
            from steward.infra.health.collect import collect_estate_health
            from steward.infra.health.snapshots import write_health_snapshot

            # Prefer inventory.db parent (works for STEWARD_DB_PATH test overrides).
            try:
                ddir = _Path(target).expanduser().resolve().parent
            except OSError:
                from steward.infra.db.settings import data_dir as _data_dir

                ddir = _data_dir()
            health_report = collect_estate_health(
                db_path=target,
                quick=True,
                probes=True,
                refresh_rollups=False,  # already refreshed above
            )
            write_health_snapshot(health_report, data_dir=ddir)
        except Exception as exc:  # noqa: BLE001 — snapshot is best-effort telemetry
            log_swallowed_error(
                "status.refresh.health_snapshot",
                exc,
                context={"db_path": str(target)},
            )

    if json_output:
        print(json.dumps(status_to_dict(report)))
        if not report.audit_chain.ok and not report.audit_chain.skipped:
            raise typer.Exit(1)
        return

    console.print(_render_inventory(report))
    if report.rollups is not None and report.rollups.used_cache:
        console.print(f"[dim]inventory counts from rollup cache (refreshed {report.rollups.refreshed_at})[/dim]")
    elif refresh:
        console.print("[dim]inventory rollups refreshed[/dim]")
    console.print(_render_latest_scan(report))
    console.print(_render_stash(report))
    console.print(_render_adapter(report, attr="last_replicate", title="last replicate"))
    console.print(_render_adapter(report, attr="last_archive", title="last archive"))
    console.print(_render_audit(report))
    if report.audit_chain.skipped:
        console.print("[dim]audit chain: skipped (--quick)[/dim]")

    if not report.audit_chain.ok and not report.audit_chain.skipped:
        raise typer.Exit(1)


__all__ = ["app"]
