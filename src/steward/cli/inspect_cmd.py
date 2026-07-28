# SPDX-License-Identifier: Apache-2.0

"""``steward inspect <hash | path>`` — read-only permanode/claim viewer.

Two output modes:

* default — Rich tables, human-readable.
* ``--json`` — a single JSON document on stdout. Suitable for piping
  into ``jq`` or for scripted consumers (CI, sub-agents).

Both modes support ``--machine <id-or-prefix>`` to filter the claims
table to a single machine_id (useful once the inventory carries rows
from multiple machines — see ``steward machines list``).
"""
from __future__ import annotations

import json
from dataclasses import asdict

import typer
from rich.console import Console
from rich.table import Table

from steward.infra.db.inspect import InspectResult, inspect

app = typer.Typer(
    name="inspect",
    help="Inspect a permanode by canonical hash or any claim path.",
    invoke_without_command=True,
)
console = Console()


@app.callback(invoke_without_command=True)
def inspect_cmd(
    target: str = typer.Argument(..., help="Canonical hash OR file path to inspect."),
    machine: str | None = typer.Option(
        None,
        "--machine",
        help="Filter claims to a single machine_id (full UUID or any unique prefix).",
    ),
    include_imports: bool = typer.Option(
        False,
        "--include-imports",
        help="Also resolve the target against attached (imported) "
        "inventories (ADR-0013). Claims and audit rows from attached "
        "schemas appear alongside, each tagged with its source.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a single JSON document on stdout instead of Rich tables.",
    ),
) -> None:
    """Look up a permanode by its canonical hash (xxh3 or blake3), permanode
    id, or any claim's file_path. Display the permanode + all claims + the
    most recent audit rows touching it."""
    result = inspect(target, include_imports=include_imports)
    if result is None:
        if json_output:
            print(json.dumps({"found": False, "target": target}))
            raise typer.Exit(1)
        console.print(f"[red]✗[/red] no permanode found for: {target}")
        raise typer.Exit(1)

    # Apply the optional machine filter to the claims list.
    if machine is not None:
        result = _filter_by_machine(result, machine=machine)
        if result is None:
            if json_output:
                print(
                    json.dumps(
                        {
                            "found": False,
                            "target": target,
                            "error": f"no machine_id matches prefix {machine!r}",
                        }
                    )
                )
                raise typer.Exit(1)
            console.print(
                f"[red]✗[/red] no machine_id matches prefix {machine!r}"
            )
            raise typer.Exit(1)

    if json_output:
        print(json.dumps(_to_json(result), default=str))
        return
    _render(result)


def _filter_by_machine(
    r: InspectResult, *, machine: str
) -> InspectResult | None:
    """Return a copy of ``r`` with claims filtered to machine_ids that
    start with ``machine``. Returns ``None`` when no claim matches the
    prefix (the operator's prefix is wrong — distinct error from
    "permanode not found")."""
    matching = [
        c for c in r.claims if str(c.get("machine_id", "")).startswith(machine)
    ]
    if not matching:
        return None
    return InspectResult(
        permanode_id=r.permanode_id,
        canonical_hash=r.canonical_hash,
        canonical_hash_algo=r.canonical_hash_algo,
        size_bytes=r.size_bytes,
        first_seen_at=r.first_seen_at,
        last_seen_at=r.last_seen_at,
        claims=matching,
        audit_rows=r.audit_rows,
    )


def _to_json(r: InspectResult) -> dict[str, object]:
    """Convert to a JSON-friendly dict with ``found: true`` discriminator."""
    payload = asdict(r)
    payload["found"] = True
    return payload


def _render(r: InspectResult) -> None:
    console.print(f"[bold]Permanode[/bold]  {r.permanode_id}")
    console.print(
        f"  canonical_hash    {r.canonical_hash}  ({r.canonical_hash_algo})"
    )
    console.print(f"  size_bytes        {r.size_bytes:,}")
    console.print(f"  first_seen_at     {r.first_seen_at}")
    console.print(f"  last_seen_at      {r.last_seen_at}")

    if r.claims:
        has_source = any("source" in c for c in r.claims)
        tbl = Table(title=f"Claims ({len(r.claims)})", show_lines=False)
        cols = ["id", "tier", "volume", "is_current", "path"]
        if has_source:
            cols.insert(0, "source")
        for col in cols:
            tbl.add_column(col)
        for c in r.claims:
            row_vals = []
            if has_source:
                src = c.get("source", "local")
                row_vals.append(
                    "[green]local[/green]" if src == "local"
                    else "[yellow]attached[/yellow]"
                )
            row_vals.extend([
                str(c["id"]),
                str(c["tier"]),
                str(c["volume"]),
                "yes" if c["is_current"] else "no",
                str(c["file_path"]),
            ])
            tbl.add_row(*row_vals)
        console.print(tbl)
    else:
        console.print("[dim]no claims for this permanode[/dim]")

    if r.audit_rows:
        tbl = Table(title=f"Audit ({len(r.audit_rows)} most recent)")
        for col in ("id", "timestamp", "action", "actor"):
            tbl.add_column(col)
        for a in r.audit_rows:
            tbl.add_row(
                str(a["id"]),
                str(a["timestamp"]),
                str(a["action"]),
                str(a["actor"]),
            )
        console.print(tbl)
