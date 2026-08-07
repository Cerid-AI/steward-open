# SPDX-License-Identifier: Apache-2.0

"""``steward health show|check`` — estate health gate (ADR-0017).

* ``show`` — human-oriented Rich sections + overall banner.
* ``check`` — automation exit codes + optional JSON; default ``--quick``.

Both share :func:`steward.infra.health.collect_estate_health`.
No filesystem tier mutation; snapshot write is data-dir telemetry only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from steward.core.health.evaluate import evaluate_fail_on, validate_fail_on_tokens
from steward.core.health.thresholds import (
    DEFAULT_CHECK_FAIL_ON,
    DEFAULT_THRESHOLDS,
    KNOWN_FAIL_ON_TOKENS,
    HealthThresholds,
)
from steward.infra.db.admin import migrate
from steward.infra.db.settings import data_dir, inventory_db_path
from steward.infra.health import collect_estate_health, estate_health_to_dict, write_health_snapshot
from steward.infra.status import _format_bytes

app = typer.Typer(
    name="health",
    help="Estate health gate — scan freshness, audit, stash, FP, mounts (ADR-0017).",
    no_args_is_help=True,
)
console = Console()


def _resolve_db(db: Path | None) -> Path:
    if db is not None:
        return db.expanduser()
    return inventory_db_path()


def _ensure_db(target: Path, *, json_output: bool) -> Path:
    if not target.exists():
        if json_output:
            print(json.dumps({"ok": False, "error": f"inventory.db not found at {target}"}))
            raise typer.Exit(2)
        console.print(f"[yellow]inventory.db missing at {target} — running migrate first[/yellow]")
        migrate(target)
    return target


def _parse_fail_on(values: list[str] | None) -> frozenset[str]:
    """Parse repeatable / comma-separated --fail-on tokens."""
    if not values:
        return DEFAULT_CHECK_FAIL_ON
    tokens: set[str] = set()
    for raw in values:
        for part in raw.split(","):
            part = part.strip()
            if part:
                tokens.add(part)
    return frozenset(tokens)


def _thresholds_from_opts(
    *,
    scan_max_age_hours: float,
    stash_grace_hours: float,
    adapter_max_age_hours: float,
    rollup_max_age_hours: float,
    attached_max_age_days: float,
    cooling_off_days: int = 7,
) -> HealthThresholds:
    base = DEFAULT_THRESHOLDS
    return HealthThresholds(
        scan_max_age_hours=scan_max_age_hours,
        stash_grace_hours=stash_grace_hours,
        cooling_off_days=cooling_off_days,
        adapter_max_age_hours=adapter_max_age_hours,
        rollup_max_age_hours=rollup_max_age_hours,
        attached_max_age_days=attached_max_age_days,
        free_bytes_min=base.free_bytes_min,
        free_ratio_min=base.free_ratio_min,
        sample_latency_warn_ms=base.sample_latency_warn_ms,
        unfinished_scan_warn_hours=base.unfinished_scan_warn_hours,
    )


def _should_write_snapshot(flag: bool | None, *, default_env: bool = False) -> bool:
    if flag is True:
        return True
    if flag is False:
        return False
    if default_env and os.environ.get("STEWARD_HEALTH_SNAPSHOT", "").strip() in (
        "1",
        "true",
        "yes",
    ):
        return True
    return False


def _level_style(level: str) -> str:
    return {
        "ok": "green",
        "warn": "yellow",
        "fail": "red",
        "unknown": "dim",
        "skipped": "dim",
    }.get(level, "white")


def _render_banner(overall: str) -> None:
    style = _level_style(overall)
    console.print(f"[bold {style}]overall: {overall}[/bold {style}]")


def _render_show(report: Any) -> None:
    _render_banner(report.overall)
    inv = report.inventory
    t = Table(show_header=False, title="inventory", title_justify="left")
    t.add_column("k")
    t.add_column("v", justify="right")
    t.add_row("permanodes", f"{inv.permanodes:,}")
    t.add_row("current claims", f"{inv.current_claims:,}")
    t.add_row("scan runs", f"{inv.scan_runs:,}")
    t.add_row("audit entries", f"{inv.audit_entries:,}")
    t.add_row("counts source", inv.counts_source)
    if inv.audit_skipped:
        t.add_row("audit chain", "[dim]skipped (--quick)[/dim]")
    elif inv.audit_ok is True:
        t.add_row("audit chain", "[green]ok[/green]")
    elif inv.audit_ok is False:
        t.add_row("audit chain", f"[red]BROKEN[/red] {inv.audit_error or ''}")
    console.print(t)

    st = Table(show_header=True, title="scan freshness", title_justify="left")
    st.add_column("root")
    st.add_column("age_h", justify="right")
    st.add_column("level")
    st.add_column("finished_at")
    if not report.scan_freshness:
        st.add_row("[dim]no finished scans[/dim]", "", "", "")
    for r in report.scan_freshness:
        age = f"{r.age_hours:.1f}" if r.age_hours is not None else "—"
        st.add_row(
            r.root_path,
            age,
            f"[{_level_style(r.level)}]{r.level}[/{_level_style(r.level)}]",
            r.finished_at or "—",
        )
    console.print(st)

    sh = report.stash
    s = Table(show_header=False, title="stash", title_justify="left")
    s.add_column("k")
    s.add_column("v", justify="right")
    s.add_row("in_flight", f"{sh.in_flight_entries:,}")
    s.add_row("source", sh.source)
    s.add_row(
        "level",
        f"[{_level_style(sh.level)}]{sh.level}[/{_level_style(sh.level)}]",
    )
    if sh.oldest_ts_iso:
        s.add_row("oldest", sh.oldest_ts_iso)
    console.print(s)

    if report.fp.present:
        fp = report.fp
        f = Table(show_header=False, title="fp", title_justify="left")
        f.add_column("k")
        f.add_column("v", justify="right")
        f.add_row("layout", str(fp.layout))
        f.add_row(
            "cloud_retire_ready",
            (
                "[green]yes[/green]"
                if fp.cloud_retire_ready
                else "[red]no[/red]"
                if fp.cloud_retire_ready is False
                else "—"
            ),
        )
        f.add_row(
            "level",
            f"[{_level_style(fp.level)}]{fp.level}[/{_level_style(fp.level)}]",
        )
        console.print(f)

    if report.mounts:
        m = Table(show_header=True, title="mounts", title_justify="left")
        m.add_column("root")
        m.add_column("present")
        m.add_column("free")
        m.add_column("latency_ms", justify="right")
        m.add_column("level")
        for p in report.mounts[:12]:
            free = _format_bytes(p.free_bytes) if p.free_bytes is not None else "—"
            lat = f"{p.sample_latency_ms:.0f}" if p.sample_latency_ms is not None else "—"
            m.add_row(
                p.root,
                "yes" if p.present else "no",
                free,
                lat,
                f"[{_level_style(p.level)}]{p.level}[/{_level_style(p.level)}]",
            )
        console.print(m)

    ck = Table(show_header=True, title="checks", title_justify="left")
    ck.add_column("name")
    ck.add_column("level")
    ck.add_column("message")
    for c in report.checks:
        ck.add_row(
            c.name,
            f"[{_level_style(c.level)}]{c.level}[/{_level_style(c.level)}]",
            c.message,
        )
    console.print(ck)

    for note in report.notes:
        console.print(f"[dim]note: {note}[/dim]")


@app.command("show")
def show_cmd(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    full: bool = typer.Option(
        False,
        "--full",
        help="Full audit walk + stash CTE (default is cheap/quick path).",
    ),
    quick: bool = typer.Option(
        False,
        "--quick",
        help="Force quick path (default for show is quick unless --full).",
    ),
    include_imports: bool = typer.Option(
        False,
        "--include-imports",
        help="Include attached inventory freshness (ADR-0013).",
    ),
    probes: bool = typer.Option(
        True,
        "--probes/--no-probes",
        help="Live mount free/total + latency probes (default: on).",
    ),
    write_snapshot: bool = typer.Option(
        False,
        "--write-snapshot/--no-write-snapshot",
        help="Append compact snapshot under data-dir/health/.",
    ),
    db: Path | None = typer.Option(None, "--db", help="Override inventory.db path."),
    scan_max_age_hours: float = typer.Option(168.0, "--scan-max-age-hours"),
    stash_grace_hours: float = typer.Option(24.0, "--stash-grace-hours"),
    adapter_max_age_hours: float = typer.Option(168.0, "--adapter-max-age-hours"),
    rollup_max_age_hours: float = typer.Option(24.0, "--rollup-max-age-hours"),
    attached_max_age_days: float = typer.Option(30.0, "--attached-max-age-days"),
) -> None:
    """Human-oriented estate health pane."""
    target = _ensure_db(_resolve_db(db), json_output=json_output)
    use_quick = not full
    if quick:
        use_quick = True
    thr = _thresholds_from_opts(
        scan_max_age_hours=scan_max_age_hours,
        stash_grace_hours=stash_grace_hours,
        adapter_max_age_hours=adapter_max_age_hours,
        rollup_max_age_hours=rollup_max_age_hours,
        attached_max_age_days=attached_max_age_days,
    )
    try:
        report = collect_estate_health(
            db_path=target,
            quick=use_quick,
            include_imports=include_imports,
            probes=probes,
            thresholds=thr,
        )
    except Exception as exc:  # noqa: BLE001 — surface as exit 2
        if json_output:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            console.print(f"[red]health collect failed: {exc}[/red]")
        raise typer.Exit(2) from exc

    if _should_write_snapshot(write_snapshot):
        try:
            write_health_snapshot(report, data_dir=data_dir())
        except Exception as exc:  # noqa: BLE001
            from steward.infra.observability.swallowed import log_swallowed_error

            log_swallowed_error("cli.health.show.snapshot", exc, context={})

    if json_output:
        print(json.dumps(estate_health_to_dict(report)))
        return

    _render_show(report)


@app.command("check")
def check_cmd(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    full: bool = typer.Option(
        False,
        "--full",
        help="Full audit walk + stash CTE (default: --quick).",
    ),
    quick: bool = typer.Option(
        True,
        "--quick/--no-quick",
        help="Cheap path (default on for check).",
    ),
    include_imports: bool = typer.Option(
        False,
        "--include-imports",
        help="Include attached inventory freshness.",
    ),
    probes: bool = typer.Option(
        False,
        "--probes/--no-probes",
        help="Live mount probes (default: off for check).",
    ),
    fail_on: list[str] | None = typer.Option(
        None,
        "--fail-on",
        help=(
            "Comma-separated or repeatable fail tokens: "
            + ",".join(sorted(KNOWN_FAIL_ON_TOKENS))
            + ". Default: stale_scan,broken_audit,stash_overdue,rollup_stale."
        ),
    ),
    write_snapshot: bool = typer.Option(
        False,
        "--write-snapshot",
        help="Append compact snapshot (also STEWARD_HEALTH_SNAPSHOT=1).",
    ),
    db: Path | None = typer.Option(None, "--db", help="Override inventory.db path."),
    scan_max_age_hours: float = typer.Option(168.0, "--scan-max-age-hours"),
    stash_grace_hours: float = typer.Option(24.0, "--stash-grace-hours"),
    adapter_max_age_hours: float = typer.Option(168.0, "--adapter-max-age-hours"),
    rollup_max_age_hours: float = typer.Option(24.0, "--rollup-max-age-hours"),
    attached_max_age_days: float = typer.Option(30.0, "--attached-max-age-days"),
) -> None:
    """Automation gate: exit 0/1/2 based on --fail-on thresholds."""
    target = _resolve_db(db)
    if not target.exists():
        if json_output:
            print(json.dumps({"ok": False, "error": f"inventory.db not found at {target}"}))
        else:
            console.print(f"[red]inventory.db not found at {target}[/red]")
        raise typer.Exit(2)

    tokens = _parse_fail_on(fail_on)
    unknown = validate_fail_on_tokens(tokens)
    if unknown:
        msg = f"unknown --fail-on token(s): {', '.join(unknown)}; known: {', '.join(sorted(KNOWN_FAIL_ON_TOKENS))}"
        if json_output:
            print(json.dumps({"ok": False, "error": msg, "unknown": unknown}))
        else:
            console.print(f"[red]{msg}[/red]")
        raise typer.Exit(2)

    use_quick = quick and not full
    thr = _thresholds_from_opts(
        scan_max_age_hours=scan_max_age_hours,
        stash_grace_hours=stash_grace_hours,
        adapter_max_age_hours=adapter_max_age_hours,
        rollup_max_age_hours=rollup_max_age_hours,
        attached_max_age_days=attached_max_age_days,
    )
    try:
        report = collect_estate_health(
            db_path=target,
            quick=use_quick,
            include_imports=include_imports,
            probes=probes,
            thresholds=thr,
        )
    except Exception as exc:  # noqa: BLE001
        if json_output:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            console.print(f"[red]health collect failed: {exc}[/red]")
        raise typer.Exit(2) from exc

    if _should_write_snapshot(write_snapshot or None, default_env=True):
        try:
            write_health_snapshot(report, data_dir=data_dir())
        except Exception as exc:  # noqa: BLE001
            from steward.infra.observability.swallowed import log_swallowed_error

            log_swallowed_error("cli.health.check.snapshot", exc, context={})

    failed = evaluate_fail_on(report, tokens, thresholds=thr)
    ok = len(failed) == 0
    payload: dict[str, Any] = {
        "ok": ok,
        "overall": report.overall,
        "failed": [
            {"name": c.name, "level": c.level, "message": c.message, "details": c.details}
            for c in failed
        ],
        "fail_on": sorted(tokens),
        "quick": report.quick,
    }
    if json_output:
        payload["report"] = estate_health_to_dict(report)
        print(json.dumps(payload))
    else:
        style = "green" if ok else "red"
        console.print(f"[{style}]health check {'ok' if ok else 'FAIL'}[/{style}] (overall={report.overall})")
        for c in failed:
            console.print(f"  [red]• {c.name}[/red]: {c.message}")

    raise typer.Exit(0 if ok else 1)


__all__ = ["app", "check_cmd", "show_cmd"]
