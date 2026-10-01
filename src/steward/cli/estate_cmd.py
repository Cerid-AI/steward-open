# SPDX-License-Identifier: Apache-2.0

"""``steward estate`` — the multi-host estate file and this host's place in it.

Subcommands:

* ``show`` — the loaded estate, with the volumes as this host sees them.
* ``validate [PATH]`` — parse + validate an estate file; exit 0 valid, 1 not.
* ``whoami`` — host id, role, data dir, and whether inventory.db is bound to this host.
* ``check`` — live mounts vs the estate, forbidden mounts, data dir, DB binding;
  exit 1 on any problem. Also counts what ``enforce`` would have refused so
  far (the ``ownership_would_refuse`` audit rows report mode writes).

All are read-only. Without an estate file Steward runs in legacy single-host
mode and these commands say so. An invalid estate file exits 2.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from steward.core.errors import DataDirUnavailableError, EstateError
from steward.core.estate import LEGACY_HOST, Estate
from steward.infra.db.admin import read_machine_id
from steward.infra.db.settings import assert_data_dir_mounted, inventory_db_path, resolve_data_dir
from steward.infra.estate import (
    Binding,
    Finding,
    HostIdentity,
    check_binding,
    check_mounts,
    current,
    estate_config_path,
    load_estate,
    resolve_host,
    system_hostname,
)
from steward.infra.estate.guard import WouldRefuse, would_refuse_counts

app = typer.Typer(
    name="estate",
    help="Inspect the multi-host estate file and this host's identity in it.",
    no_args_is_help=True,
)
console = Console()


def _load(json_output: bool) -> Estate | None:
    try:
        return current()
    except EstateError as exc:
        if json_output:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(2) from exc


def _warn_unknown(identity: HostIdentity) -> None:
    if not identity.known:
        console.print(f"[yellow]warning: this machine is not an estate host — {identity.reason}[/yellow]")
        console.print("[yellow]read commands continue; mutating commands will refuse.[/yellow]")


def _data_dir_finding(data_dir: Path) -> Finding | None:
    try:
        assert_data_dir_mounted(data_dir)
    except DataDirUnavailableError as exc:
        return Finding("problem", "data_dir_unmounted", str(exc), str(data_dir))
    return None


def _binding(estate: Estate, identity: HostIdentity) -> Binding | None:
    if identity.host_id is None:
        return None
    return check_binding(estate.hosts[identity.host_id], read_machine_id(inventory_db_path()))


@app.command("show")
def show_cmd(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """The loaded estate: hosts, and every volume as this host sees it."""
    path = estate_config_path()
    estate = _load(json_output)
    if estate is None:
        if json_output:
            print(json.dumps({"path": str(path), "estate": None, "host": LEGACY_HOST}))
        else:
            console.print(f"[dim]no estate file at {path} — legacy single-host mode[/dim]")
        return
    identity = resolve_host(estate)
    me = identity.host_id

    if json_output:
        view = {
            vol_id: vol.mounts[me].model_dump(mode="json")
            for vol_id, vol in estate.volumes.items()
            if me is not None and me in vol.mounts
        }
        print(
            json.dumps(
                {
                    "path": str(path),
                    "host": asdict(identity),
                    "estate": estate.model_dump(mode="json"),
                    "this_host_mounts": view,
                }
            )
        )
        return

    _warn_unknown(identity)
    name = (estate.metadata or {}).get("name", "—")
    console.print(f"[bold]estate[/bold] {name}  [dim]{path}[/dim]  enforcement: {estate.enforcement}")

    hosts = Table(title="hosts", title_justify="left", show_header=True, header_style="bold")
    for col in ("id", "role", "hostnames", "machine_id", "data_dir"):
        hosts.add_column(col)
    for host_id, host in estate.hosts.items():
        marker = " [green](this host)[/green]" if host_id == me else ""
        hosts.add_row(
            host_id + marker,
            host.role,
            ", ".join(host.hostnames) or "—",
            host.machine_id or "—",
            host.data_dir or "—",
        )
    console.print(hosts)

    title = f"volumes as seen from {me!r}" if me is not None else "volumes"
    vols = Table(title=title, title_justify="left", show_header=True, header_style="bold")
    for col in ("id", "tier", "kind", "owner", "plan", "mount", "access", "criticality", "scan"):
        vols.add_column(col)
    for vol_id, vol in estate.volumes.items():
        mount = vol.mounts.get(me) if me is not None else None
        if me is not None and mount is None:
            continue
        if mount is None:
            mount_cell = ", ".join(f"{h}:{m.path} ({m.access})" for h, m in vol.mounts.items())
            access = criticality = scan = "—"
        else:
            mount_cell, access, criticality = mount.path, mount.access, mount.criticality
            scan = "yes" if vol.scan and mount.scan else "no"
        vols.add_row(vol_id, vol.tier, vol.kind, vol.owner, vol.plan, mount_cell, access, criticality, scan)
    console.print(vols)
    if me is not None:
        unmounted = sum(1 for vol in estate.volumes.values() if me not in vol.mounts)
        if unmounted:
            console.print(f"[dim]{unmounted} volume(s) have no mount on {me!r}[/dim]")


@app.command("validate")
def validate_cmd(
    path: Path | None = typer.Argument(None, help="Estate YAML to validate (default: the configured estate file)."),
) -> None:
    """Parse + validate an estate file; exits 0 if valid, 1 otherwise."""
    target = path.expanduser() if path is not None else estate_config_path()
    try:
        estate = load_estate(target)
    except EstateError as exc:
        console.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]✓[/green] {target}: valid estate v{estate.version} "
        f"({len(estate.hosts)} hosts, {len(estate.volumes)} volumes, enforcement {estate.enforcement})"
    )


@app.command("whoami")
def whoami_cmd(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """This machine's host id, role, data dir, and inventory.db binding."""
    estate = _load(json_output)
    data_dir, source = resolve_data_dir()
    db_path = inventory_db_path()
    mount_problem = _data_dir_finding(data_dir)
    out: dict[str, Any] = {
        "estate_path": str(estate_config_path()) if estate is not None else None,
        "hostname": system_hostname(),
        "data_dir": str(data_dir),
        "data_dir_source": source,
        "data_dir_mounted": mount_problem is None,
        "db_path": str(db_path),
    }
    identity: HostIdentity | None = None
    binding: Binding | None = None
    if estate is None:
        out.update(host_id=LEGACY_HOST, known=True, role="primary", reason="no estate file (legacy)")
        out.update(binding="legacy", db_machine_id=read_machine_id(db_path), expected_machine_id=None)
    else:
        identity = resolve_host(estate)
        binding = _binding(estate, identity)
        out.update(host_id=identity.host_id, known=identity.known, reason=identity.reason)
        out["role"] = estate.hosts[identity.host_id].role if identity.host_id is not None else None
        if binding is None:
            out.update(binding="unknown-host", db_machine_id=read_machine_id(db_path), expected_machine_id=None)
        else:
            out.update(binding=binding.status, db_machine_id=binding.actual, expected_machine_id=binding.expected)

    if json_output:
        print(json.dumps(out))
        return

    if identity is not None:
        _warn_unknown(identity)
    t = Table(show_header=False, title="whoami", title_justify="left")
    t.add_column("k")
    t.add_column("v")
    t.add_row("host", f"{out['host_id'] or '[red]unknown[/red]'}  [dim]{out['reason']}[/dim]")
    t.add_row("role", out["role"] or "—")
    t.add_row("hostname", out["hostname"])
    t.add_row("estate", out["estate_path"] or "[dim]none (legacy single-host mode)[/dim]")
    mounted = "" if out["data_dir_mounted"] else "  [red]volume not mounted[/red]"
    t.add_row("data dir", f"{out['data_dir']}  [dim]({source})[/dim]{mounted}")
    t.add_row("inventory.db", out["db_path"])
    t.add_row("db machine_id", out["db_machine_id"] or "—")
    binding_style = {"bound": "green", "mismatch": "red", "missing": "red"}.get(out["binding"], "dim")
    binding_text = binding.describe() if binding is not None else out["binding"]
    t.add_row("binding", f"[{binding_style}]{binding_text}[/{binding_style}]")
    console.print(t)


@app.command("check")
def check_cmd(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Live mounts vs the estate, forbidden mounts, data dir and DB binding; exit 1 on problems."""
    estate = _load(json_output)
    if estate is None:
        if json_output:
            print(json.dumps({"ok": True, "estate": None, "findings": []}))
        else:
            console.print(f"[dim]no estate file at {estate_config_path()} — nothing to check (legacy mode)[/dim]")
        return

    identity = resolve_host(estate)
    findings: list[Finding] = []
    binding: Binding | None = None
    would_refuse: list[WouldRefuse] = []
    if identity.host_id is None:
        findings.append(Finding("problem", "unknown_host", identity.reason))
    else:
        findings.extend(check_mounts(estate, identity.host_id))
        data_dir, _ = resolve_data_dir()
        mount_problem = _data_dir_finding(data_dir)
        if mount_problem is not None:
            findings.append(mount_problem)
        binding = _binding(estate, identity)
        if binding is not None and binding.status in ("mismatch", "missing"):
            findings.append(Finding("problem", "binding", binding.describe(), str(inventory_db_path())))
        if mount_problem is None and binding is not None and binding.ok:
            would_refuse = would_refuse_counts(inventory_db_path())

    problems = [f for f in findings if f.level == "problem"]
    if json_output:
        print(
            json.dumps(
                {
                    "ok": not problems,
                    "host": asdict(identity),
                    "binding": asdict(binding) if binding is not None else None,
                    "findings": [asdict(f) for f in findings],
                    "would_refuse": [asdict(w) for w in would_refuse],
                }
            )
        )
        raise typer.Exit(1 if problems else 0)

    console.print(f"host: {identity.host_id or '[red]unknown[/red]'}  [dim]{identity.reason}[/dim]")
    if binding is not None:
        console.print(f"binding: {binding.describe()}")
    if findings:
        t = Table(show_header=True, header_style="bold")
        for col in ("level", "check", "volume", "detail"):
            t.add_column(col)
        for f in findings:
            style = "red" if f.level == "problem" else "yellow"
            t.add_row(f"[{style}]{f.level}[/{style}]", f.kind, f.volume_id or "—", f.message)
        console.print(t)
    if would_refuse:
        w = Table(
            title="enforce would refuse (ownership_would_refuse audit rows)",
            title_justify="left",
            show_header=True,
            header_style="bold",
        )
        for col in ("refusal", "check", "volume", "items", "events"):
            w.add_column(col)
        for item in would_refuse:
            w.add_row(item.refusal, item.check, item.volume_id or "—", f"{item.count:,}", f"{item.events:,}")
        console.print(w)
    if problems:
        console.print(f"[red]✗ {len(problems)} problem(s)[/red]")
        raise typer.Exit(1)
    console.print("[green]✓ estate matches this host[/green]")


__all__ = ["app", "check_cmd", "show_cmd", "validate_cmd", "whoami_cmd"]
