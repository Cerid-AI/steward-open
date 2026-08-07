# SPDX-License-Identifier: Apache-2.0

"""``steward machines`` — list, inspect, and fleet health matrix.

Subcommands:

* ``list`` — one row per machine_id with counts + first/last seen.
* ``show <id>`` — full details for one machine + recent scan_runs +
  recent audit entries.
* ``health`` — multi-machine fleet matrix (last scan, chain, envelope
  SLA; ADR-0021). Defaults to ``--include-imports``.

All are read-only. On a single-machine setup this is mostly a sanity
check; on a multi-machine setup it's the entry point for cross-machine
debugging and envelope sync SLA gates.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from steward.infra.db.admin import migrate
from steward.infra.db.settings import inventory_db_path
from steward.infra.machines import get_machine, list_machines

app = typer.Typer(
    name="machines",
    help="List and inspect machine_ids that have touched the inventory.",
    invoke_without_command=True,
    no_args_is_help=True,
)
console = Console()


def _ensure_db_ready() -> Path:
    target = inventory_db_path()
    if not target.exists():
        console.print(f"[yellow]inventory.db missing at {target} — running migrate first[/yellow]")
        migrate(target)
    return target


@app.command("list")
def list_cmd(
    include_imports: bool = typer.Option(
        False,
        "--include-imports",
        help="Also include machine_ids from attached (imported) inventories (ADR-0013). Default is local-only.",
    ),
) -> None:
    """One row per machine that has ever touched the inventory.

    With ``--include-imports``, attached inventories' machine_ids
    appear alongside; the ``source`` column marks each row as
    ``local`` or ``attached`` so the operator can tell them apart.
    """
    target = _ensure_db_ready()
    summaries = list_machines(db_path=target, include_imports=include_imports)
    if not summaries:
        console.print("[dim]no machines on record yet (run `steward scan` first)[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("machine_id")
    table.add_column("current?", justify="center")
    if include_imports:
        table.add_column("source")
    table.add_column("claims", justify="right")
    table.add_column("current_claims", justify="right")
    table.add_column("scan_runs", justify="right")
    table.add_column("audit_entries", justify="right")
    table.add_column("last_seen")
    for s in summaries:
        row = [
            s.machine_id[:8] + "…" if len(s.machine_id) > 12 else s.machine_id,
            "[green]yes[/green]" if s.is_current else "[dim]no[/dim]",
        ]
        if include_imports:
            source_marker = "[green]local[/green]" if s.source == "local" else "[yellow]attached[/yellow]"
            row.append(source_marker)
        row.extend(
            [
                f"{s.claim_count:,}",
                f"{s.current_claim_count:,}",
                f"{s.scan_run_count:,}",
                f"{s.audit_entry_count:,}",
                (s.last_seen_at or "—"),
            ]
        )
        table.add_row(*row)
    console.print(table)


@app.command("show")
def show_cmd(
    machine_id: str = typer.Argument(
        ...,
        help="Full machine_id UUID, or any unique prefix.",
    ),
    include_imports: bool = typer.Option(
        False,
        "--include-imports",
        help="Also resolve the id against attached (imported) "
        "inventories (ADR-0013). Recent scan_runs / audit rows are "
        "pulled from the schema where the machine was found.",
    ),
) -> None:
    """Full details: counts, first/last seen, recent scan_runs, recent audit."""
    target = _ensure_db_ready()
    # Resolve prefix → full id (operator-friendly).
    summaries = list_machines(db_path=target, include_imports=include_imports)
    matches = [s for s in summaries if s.machine_id.startswith(machine_id)]
    if not matches:
        console.print(f"[red]no machine matches {machine_id!r}[/red]")
        raise typer.Exit(2)
    if len(matches) > 1:
        console.print(f"[red]ambiguous prefix {machine_id!r} — matches {len(matches)} machines[/red]")
        for m in matches:
            console.print(f"  {m.machine_id}")
        raise typer.Exit(2)
    resolved = matches[0].machine_id

    details = get_machine(
        db_path=target,
        machine_id=resolved,
        include_imports=include_imports,
    )
    if details is None:
        # Shouldn't happen — list_machines just returned this id.
        console.print(f"[red]machine {resolved} disappeared between queries[/red]")
        raise typer.Exit(2)

    s = details.summary
    header = Table(show_header=False, title=resolved, title_justify="left")
    header.add_column("k")
    header.add_column("v", justify="right")
    header.add_row(
        "current?",
        "[green]yes[/green]" if s.is_current else "[dim]no[/dim]",
    )
    if include_imports:
        header.add_row(
            "source",
            ("[green]local[/green]" if s.source == "local" else "[yellow]attached[/yellow]"),
        )
    header.add_row("claims (total / current)", f"{s.claim_count:,} / {s.current_claim_count:,}")
    header.add_row("scan_runs", f"{s.scan_run_count:,}")
    header.add_row("audit_entries", f"{s.audit_entry_count:,}")
    header.add_row("first_seen_at", s.first_seen_at or "—")
    header.add_row("last_seen_at", s.last_seen_at or "—")
    console.print(header)

    if details.recent_scan_runs:
        t = Table(title="recent scan_runs", title_justify="left", show_header=True)
        t.add_column("timestamp", width=20)
        t.add_column("summary")
        for a in details.recent_scan_runs:
            t.add_row(a.timestamp[:19], a.summary)
        console.print(t)

    if details.recent_audit:
        t = Table(title="recent audit", title_justify="left", show_header=True)
        t.add_column("timestamp", width=20)
        t.add_column("summary")
        for a in details.recent_audit:
            t.add_row(a.timestamp[:19], a.summary)
        console.print(t)


__all__ = ["app", "health_cmd", "list_cmd", "show_cmd"]


# ─────────────────────── fleet health matrix (ADR-0021) ──────────────────────


def _format_age_hours(age: float | None) -> str:
    if age is None:
        return "never"
    if age < 1.0:
        return f"{int(age * 60)}m"
    if age < 48.0:
        return f"{age:.1f}h"
    days = age / 24.0
    rem_h = age % 24.0
    if rem_h < 0.5:
        return f"{int(days)}d"
    return f"{int(days)}d{int(rem_h)}h"


def _level_style(level: str) -> str:
    return {
        "ok": "green",
        "warn": "yellow",
        "fail": "red",
        "unknown": "dim",
        "skipped": "dim",
    }.get(level, "white")


def _parse_fleet_fail_on(values: list[str] | None) -> frozenset[str]:
    from steward.core.fleet import DEFAULT_FLEET_CHECK_FAIL_ON

    if not values:
        return DEFAULT_FLEET_CHECK_FAIL_ON
    tokens: set[str] = set()
    for raw in values:
        for part in raw.split(","):
            part = part.strip()
            if part:
                tokens.add(part)
    return frozenset(tokens)


def _render_fleet_matrix(matrix: object) -> None:
    from steward.core.fleet.types import FleetHealthMatrix

    assert isinstance(matrix, FleetHealthMatrix)
    style = _level_style(matrix.overall)
    console.print(f"[bold {style}]overall: {matrix.overall}[/bold {style}]")

    table = Table(show_header=True, header_style="bold", title="fleet", title_justify="left")
    table.add_column("machine")
    table.add_column("src")
    table.add_column("claims", justify="right")
    table.add_column("last_scan")
    table.add_column("scan")
    table.add_column("chain")
    table.add_column("envelope")
    table.add_column("level")
    for r in matrix.rows:
        mid = r.machine_id[:8] + "…" if len(r.machine_id) > 12 else r.machine_id
        src = "[green]local[/green]" if r.source == "local" else "[yellow]attached[/yellow]"
        last_scan = _format_age_hours(r.scan_age_hours)
        table.add_row(
            mid,
            src,
            f"{r.claim_count:,}",
            last_scan,
            f"[{_level_style(r.scan_level)}]{r.scan_level}[/{_level_style(r.scan_level)}]",
            f"[{_level_style(r.chain_level)}]{r.chain_level}[/{_level_style(r.chain_level)}]",
            f"[{_level_style(r.envelope_level)}]{r.envelope_level}[/{_level_style(r.envelope_level)}]",
            f"[{_level_style(r.level)}]{r.level}[/{_level_style(r.level)}]",
        )
    console.print(table)

    sla = matrix.envelope_sla
    s = Table(show_header=False, title="envelope SLA", title_justify="left")
    s.add_column("k")
    s.add_column("v", justify="right")
    s.add_row(
        "level",
        f"[{_level_style(sla.level)}]{sla.level}[/{_level_style(sla.level)}]",
    )
    s.add_row("local_export", sla.local_export_at or "—")
    s.add_row("local_export_age", _format_age_hours(sla.local_export_age_hours))
    s.add_row("attached", str(sla.attached_count))
    s.add_row("attached_stale", str(sla.attached_stale_count))
    s.add_row("missing_payload", str(sla.attached_missing_payload))
    console.print(s)

    for note in matrix.notes:
        console.print(f"[dim]note: {note}[/dim]")


@app.command("health")
def health_cmd(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    include_imports: bool = typer.Option(
        True,
        "--include-imports/--local-only",
        help="Include attached inventories (default: on for health matrix).",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Full local audit-chain verify (default: --quick).",
    ),
    quick: bool = typer.Option(
        True,
        "--quick/--no-quick",
        help="Cheap path (default on).",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Automation gate: exit 0/1/2 based on --fail-on (implies compact summary).",
    ),
    fail_on: list[str] | None = typer.Option(
        None,
        "--fail-on",
        help=(
            "Comma-separated or repeatable fail tokens: "
            "fleet_stale_scan,fleet_chain_stale,envelope_sla,attached_missing. "
            "Default when --check: all four."
        ),
    ),
    scan_max_age_hours: float = typer.Option(168.0, "--scan-max-age-hours"),
    envelope_max_age_hours: float = typer.Option(192.0, "--envelope-max-age-hours"),
    attached_max_age_days: float = typer.Option(30.0, "--attached-max-age-days"),
    chain_verify_max_age_days: float = typer.Option(30.0, "--chain-verify-max-age-days"),
    db: Path | None = typer.Option(None, "--db", help="Override inventory.db path."),
) -> None:
    """Fleet health matrix: last scan, claims, chain age, envelope SLA (ADR-0021).

    Defaults to ``--include-imports`` (unlike ``machines list``). Use
    ``--check`` for automation exit codes.
    """
    import json

    from steward.core.fleet import (
        DEFAULT_FLEET_THRESHOLDS,
        KNOWN_FLEET_FAIL_ON_TOKENS,
        FleetThresholds,
        evaluate_fleet_fail_on,
        validate_fleet_fail_on_tokens,
    )
    from steward.infra.db.settings import data_dir, inventory_db_path
    from steward.infra.fleet import collect_fleet_health, fleet_health_to_dict

    target = db.expanduser() if db is not None else inventory_db_path()
    if not target.exists():
        if json_output or check:
            print(json.dumps({"ok": False, "error": f"inventory.db not found at {target}"}))
        else:
            console.print(f"[yellow]inventory.db missing at {target} — running migrate first[/yellow]")
            migrate(target)
        if not target.exists():
            raise typer.Exit(2)

    if check:
        tokens = _parse_fleet_fail_on(fail_on)
        unknown = validate_fleet_fail_on_tokens(tokens)
        if unknown:
            msg = (
                f"unknown --fail-on token(s): {', '.join(unknown)}; "
                f"known: {', '.join(sorted(KNOWN_FLEET_FAIL_ON_TOKENS))}"
            )
            if json_output or check:
                print(json.dumps({"ok": False, "error": msg, "unknown": unknown}))
            else:
                console.print(f"[red]{msg}[/red]")
            raise typer.Exit(2)
    else:
        tokens = frozenset()

    base = DEFAULT_FLEET_THRESHOLDS
    thr = FleetThresholds(
        scan_max_age_hours=scan_max_age_hours,
        envelope_max_age_hours=envelope_max_age_hours,
        attached_max_age_days=attached_max_age_days,
        chain_verify_max_age_days=chain_verify_max_age_days,
    )
    # silence unused if base only needed for defaults above
    _ = base

    use_quick = quick and not full
    try:
        matrix = collect_fleet_health(
            db_path=target,
            include_imports=include_imports,
            quick=use_quick,
            thresholds=thr,
            data_dir=data_dir(),
        )
    except Exception as exc:  # noqa: BLE001
        if json_output or check:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            console.print(f"[red]fleet health collect failed: {exc}[/red]")
        raise typer.Exit(2) from exc

    if check:
        failed = evaluate_fleet_fail_on(matrix, tokens, thresholds=thr)
        payload = {
            "ok": len(failed) == 0,
            "failed": [
                {
                    "name": c.name,
                    "level": c.level,
                    "message": c.message,
                    "details": c.details,
                }
                for c in failed
            ],
            "fail_on": sorted(tokens),
            "matrix": fleet_health_to_dict(matrix),
        }
        print(json.dumps(payload))
        raise typer.Exit(0 if len(failed) == 0 else 1)

    if json_output:
        print(json.dumps(fleet_health_to_dict(matrix)))
        return

    _render_fleet_matrix(matrix)
