# SPDX-License-Identifier: Apache-2.0

"""``steward fleet`` — pull other estate hosts' published inventories; the estate-wide view.

Subcommands:

* ``pull --host <id> [--dry-run | --execute]`` — on the estate primary, rsync a
  client's published envelope and health directory over ssh into
  ``<data_dir>/inbox/<host>/``, check the envelope's exporter machine_id
  against the estate, and import it (read-only ATTACH). Dry-run by default.
* ``status`` — every estate host: what it publishes, what was last pulled
  and imported, and the capacity graded from its pulled health sidecar.

Without an estate file there is no fleet: ``pull`` exits 2, ``status`` says so.
"""

from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from steward.core.errors import BindingMismatchError, EstateError
from steward.core.health.evaluate import age_hours
from steward.infra.db.admin import read_machine_id
from steward.infra.db.settings import data_dir, imports_dir, inventory_db_path
from steward.infra.estate import check_binding, current, require_binding
from steward.infra.estate.active import ActiveEstate, active_estate
from steward.infra.health.remote import collect_remote_capacity
from steward.infra.sync.importer import ImportError_
from steward.infra.sync.imports_admin import list_imports
from steward.infra.sync.pull import (
    DEFAULT_TIMEOUT_SECONDS,
    PullError,
    PullPlan,
    PullRefused,
    execute_pull,
    inbox_dir,
    plan_pull,
)

app = typer.Typer(
    name="fleet",
    help="Pull other estate hosts' published inventories over ssh; the estate-wide health view.",
    no_args_is_help=True,
)
console = Console()


def _fail(message: str, *, code: int, json_output: bool, **extra: Any) -> typer.Exit:
    if json_output:
        print(json.dumps({"ok": False, "error": message, **extra}))
    else:
        console.print(f"[red]✗[/red] {message}")
    return typer.Exit(code)


def _active(json_output: bool) -> ActiveEstate:
    try:
        return active_estate()
    except EstateError as exc:
        raise _fail(str(exc), code=2, json_output=json_output) from exc


def _plan_dict(plan: PullPlan) -> dict[str, Any]:
    return {
        "host": plan.host_id,
        "ssh": plan.ssh,
        "machine_id": plan.machine_id,
        "remote_envelope": plan.remote_envelope,
        "remote_health_dir": plan.remote_health_dir,
        "inbox": str(plan.inbox),
        "local_envelope": str(plan.local_envelope),
        "commands": [list(c) for c in plan.commands],
    }


@app.command("pull")
def pull_cmd(
    host: str = typer.Option(..., "--host", help="Estate host id to pull from (must have a publish block)."),
    execute: bool = typer.Option(
        False,
        "--execute/--dry-run",
        help="--dry-run (default) prints the rsync commands and touches nothing; --execute pulls and imports.",
    ),
    timeout: float = typer.Option(DEFAULT_TIMEOUT_SECONDS, "--timeout", help="Seconds allowed per rsync."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Pull a client's published inventory export into this primary's inbox and import it."""
    ctx = _active(json_output)
    if not ctx.configured:
        raise _fail("no estate file — fleet pull needs one (legacy single-host mode)", code=2, json_output=json_output)
    if ctx.legacy:
        assert ctx.identity is not None
        raise _fail(
            f"refused: this machine is not an estate host ({ctx.identity.reason}); only the primary pulls",
            code=1,
            json_output=json_output,
        )
    estate = ctx.estate
    db_path = inventory_db_path()
    try:
        plan = plan_pull(estate, me=ctx.host_id, host_id=host, data_dir=data_dir())
    except PullRefused as exc:
        raise _fail(f"refused: {exc}", code=1, json_output=json_output) from exc

    if not execute:
        if json_output:
            print(json.dumps({"ok": True, "mode": "dry-run", "plan": _plan_dict(plan)}))
            return
        console.print(f"[bold]dry-run[/bold] — pull {plan.host_id} ({plan.ssh}) into {plan.inbox}")
        for cmd in plan.commands:
            console.print("  " + shlex.join(cmd), markup=False, soft_wrap=True)
        console.print(f"  then require exporter machine_id {plan.machine_id} and import {plan.local_envelope}")
        console.print("[dim]nothing pulled; pass --execute to pull and import[/dim]")
        return

    try:
        require_binding(check_binding(estate.hosts[ctx.host_id], read_machine_id(db_path)))
        result = execute_pull(plan, db_path=db_path, imports_dir=imports_dir(), timeout=timeout)
    except (BindingMismatchError, PullRefused) as exc:
        raise _fail(f"refused: {exc}", code=1, json_output=json_output) from exc
    except (PullError, ImportError_) as exc:
        raise _fail(str(exc), code=1, json_output=json_output) from exc

    out = {
        "ok": True,
        "mode": "execute",
        "plan": _plan_dict(plan),
        "envelope_bytes": result.envelope_bytes,
        "unchanged": result.unchanged,
        "payload_path": str(result.imported.payload_path) if result.imported else None,
        "payload_blake3": result.imported.payload_blake3 if result.imported else None,
        "claim_rows": result.imported.claim_rows if result.imported else None,
        "replaced_existing": result.imported.replaced_existing if result.imported else None,
        "health_pulled": result.health_pulled,
        "health_error": result.health_error,
        "duration_seconds": round(result.duration_seconds, 3),
    }
    if json_output:
        print(json.dumps(out))
        return
    if result.imported is None:
        console.print(f"[green]✓[/green] pulled {plan.host_id}: inventory unchanged, already attached")
        console.print(f"  envelope   = {plan.local_envelope} ({result.envelope_bytes:,} bytes)")
    else:
        verb = "re-attached" if result.imported.replaced_existing else "attached"
        console.print(
            f"[green]✓[/green] pulled {plan.host_id}: inventory {verb} ({result.imported.claim_rows:,} claims)"
        )
        console.print(f"  envelope   = {plan.local_envelope} ({result.envelope_bytes:,} bytes)")
        console.print(f"  payload    = {result.imported.payload_path}")
    if result.health_error:
        console.print(f"  [yellow]health sidecar not pulled: {result.health_error}[/yellow]")
    elif result.health_pulled:
        console.print(f"  health     = {plan.local_health_dir}")
    console.print(f"  duration   = {result.duration_seconds:.1f}s")


def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return None


def _fmt_age(hours: float | None) -> str:
    if hours is None:
        return "—"
    return f"{hours:.1f}h" if hours < 48 else f"{hours / 24:.1f}d"


@app.command("status")
def status_cmd(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Every estate host: publish source, last pull/import, and capacity from its pulled health sidecar."""
    ctx = _active(json_output)
    if not ctx.configured:
        if json_output:
            print(json.dumps({"estate": None, "hosts": []}))
        else:
            console.print("[dim]no estate file — legacy single-host mode, no fleet to show[/dim]")
        return
    estate = current()
    assert estate is not None and ctx.identity is not None  # configured
    me = None if ctx.legacy else ctx.host_id
    base = data_dir()
    db_path = inventory_db_path()
    imports = {row.machine_id: row for row in list_imports(db_path=db_path)} if db_path.exists() else {}
    graded = {h.host_id: h for h in collect_remote_capacity(data_dir=base) or []}

    rows: list[dict[str, Any]] = []
    for host_id, host in estate.hosts.items():
        publish = host.publish
        envelope_at: str | None = None
        if publish is not None:
            envelope_at = _mtime_iso(inbox_dir(base, host_id) / PurePosixPath(publish.envelope).name)
        attached = imports.get(host.machine_id) if host.machine_id else None
        capacity = graded.get(host_id)
        rows.append(
            {
                "host": host_id,
                "role": host.role,
                "this_host": host_id == me,
                "machine_id": host.machine_id,
                "ssh": publish.ssh if publish is not None else None,
                "pulled_envelope_at": envelope_at,
                "pulled_envelope_age_hours": age_hours(envelope_at),
                "imported_at": attached.imported_at if attached is not None else None,
                "import_age_hours": age_hours(attached.imported_at) if attached is not None else None,
                "payload_exists": attached.payload_exists if attached is not None else None,
                "capacity_level": capacity.level if capacity is not None else None,
                "capacity_message": capacity.message if capacity is not None else None,
                "health_generated_at": capacity.generated_at if capacity is not None else None,
                "health_age_hours": capacity.age_hours if capacity is not None else None,
            }
        )

    if json_output:
        print(json.dumps({"estate": (estate.metadata or {}).get("name"), "host": me, "hosts": rows}))
        return

    if me is None:
        console.print(f"[yellow]warning: this machine is not an estate host — {ctx.identity.reason}[/yellow]")
    t = Table(title="fleet", title_justify="left", show_header=True, header_style="bold")
    for col in ("host", "role", "publishes", "pulled", "imported", "health", "capacity"):
        t.add_column(col)
    styles = {"ok": "green", "warn": "yellow", "fail": "red"}
    for r in rows:
        level = r["capacity_level"]
        style = styles.get(level or "", "dim")
        t.add_row(
            r["host"] + (" [green](this host)[/green]" if r["this_host"] else ""),
            r["role"],
            r["ssh"] or "—",
            _fmt_age(r["pulled_envelope_age_hours"]),
            _fmt_age(r["import_age_hours"]) + ("" if r["payload_exists"] in (None, True) else " [red]missing[/red]"),
            _fmt_age(r["health_age_hours"]),
            f"[{style}]{level or '—'}[/{style}]",
        )
    console.print(t)
    for r in rows:
        if r["capacity_level"] not in (None, "ok"):
            console.print(f"[dim]{r['capacity_message']}[/dim]")


__all__ = ["app", "pull_cmd", "status_cmd"]
