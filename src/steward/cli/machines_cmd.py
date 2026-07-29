# SPDX-License-Identifier: Apache-2.0

"""``steward machines`` — list and inspect machine_ids in the inventory.

Two subcommands:

* ``list`` — one row per machine_id with counts + first/last seen.
* ``show <id>`` — full details for one machine + recent scan_runs +
  recent audit entries.

Both are read-only. On a single-machine setup this is mostly a sanity
check ("yes, my machine_id is what I think it is"); on a multi-machine
setup it's the entry point for cross-machine debugging.
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


__all__ = ["app"]
