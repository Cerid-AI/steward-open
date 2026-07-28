# SPDX-License-Identifier: Apache-2.0

"""``steward stats`` — read-only aggregations over the inventory.

Six subcommands, each with optional ``--json`` for scripted consumers:

* (default) — overview: headline counts + top 5 tiers + top 5 domains
  + the single largest permanode.
* ``by-tier`` — claim count + permanode count + total bytes per tier.
* ``by-domain`` — same shape, keyed on ``claims.domain``.
* ``extensions [--limit N]`` — top N file extensions by total bytes.
* ``classifications [--limit N]`` — top N classification labels by
  claim count.
* ``duplicates [--limit N] [--min-claims K]`` — permanodes with the
  most current claims (the dedup-candidate list).

The aggregators sit in :mod:`steward.infra.stats`; this module is
purely formatting + the typer wiring.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from steward.infra.db.admin import migrate
from steward.infra.db.settings import inventory_db_path
from steward.infra.stats import (
    by_classification,
    by_domain,
    by_extension,
    by_tier,
    duplicate_permanodes,
    overview,
)
from steward.infra.status import _format_bytes

app = typer.Typer(
    name="stats",
    help="Read-only aggregations over the inventory.",
    invoke_without_command=True,
)
console = Console()


def _ensure_db_ready() -> Path:
    target = inventory_db_path()
    if not target.exists():
        console.print(
            f"[yellow]inventory.db missing at {target} — running migrate first[/yellow]"
        )
        migrate(target)
    return target


# ─────────────────────── overview (default) ──────────────────────────


@app.callback(invoke_without_command=True)
def stats_root(
    ctx: typer.Context,
    include_imports: bool = typer.Option(
        False,
        "--include-imports",
        help="Aggregate across local + every attached inventory's "
        "claims (ADR-0013). Default is local-only.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON instead of Rich tables.",
    ),
) -> None:
    """Print the overview when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return
    target = _ensure_db_ready()
    rep = overview(db_path=target, include_imports=include_imports)
    if json_output:
        print(json.dumps(asdict(rep), default=str))
        return

    head = Table(show_header=False, title="inventory overview", title_justify="left")
    head.add_column("k")
    head.add_column("v", justify="right")
    head.add_row("permanodes", f"{rep.permanodes:,}")
    head.add_row("current_claims", f"{rep.current_claims:,}")
    head.add_row("total_bytes", _format_bytes(rep.total_bytes))
    head.add_row("permanodes_with_duplicates", f"{rep.duplicate_count:,}")
    if rep.largest_permanode is not None:
        lp = rep.largest_permanode
        head.add_row(
            "largest_permanode",
            f"{_format_bytes(lp.size_bytes)} "
            f"({lp.current_claim_count}× current)",
        )
    console.print(head)

    if rep.top_tiers:
        t = Table(title="top tiers", show_header=True, title_justify="left")
        t.add_column("tier")
        t.add_column("claims", justify="right")
        t.add_column("permanodes", justify="right")
        t.add_column("bytes", justify="right")
        for r in rep.top_tiers:
            t.add_row(
                r.tier,
                f"{r.claim_count:,}",
                f"{r.permanode_count:,}",
                _format_bytes(r.total_bytes),
            )
        console.print(t)

    if rep.top_domains:
        t = Table(title="top domains", show_header=True, title_justify="left")
        t.add_column("domain")
        t.add_column("claims", justify="right")
        t.add_column("permanodes", justify="right")
        t.add_column("bytes", justify="right")
        for d in rep.top_domains:
            t.add_row(
                d.domain or "(none)",
                f"{d.claim_count:,}",
                f"{d.permanode_count:,}",
                _format_bytes(d.total_bytes),
            )
        console.print(t)


# ─────────────────────── per-axis subcommands ──────────────────────────


@app.command("by-tier")
def by_tier_cmd(
    include_imports: bool = typer.Option(
        False, "--include-imports",
        help="Aggregate across attached inventories too (ADR-0013).",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """One row per tier."""
    target = _ensure_db_ready()
    rows = by_tier(db_path=target, include_imports=include_imports)
    if json_output:
        print(json.dumps([asdict(r) for r in rows], default=str))
        return
    t = Table(show_header=True, title="by tier", title_justify="left")
    t.add_column("tier")
    t.add_column("claims", justify="right")
    t.add_column("permanodes", justify="right")
    t.add_column("bytes", justify="right")
    for r in rows:
        t.add_row(
            r.tier,
            f"{r.claim_count:,}",
            f"{r.permanode_count:,}",
            _format_bytes(r.total_bytes),
        )
    console.print(t)


@app.command("by-domain")
def by_domain_cmd(
    include_imports: bool = typer.Option(
        False, "--include-imports",
        help="Aggregate across attached inventories too (ADR-0013).",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """One row per ``claims.domain`` (NULLs grouped together)."""
    target = _ensure_db_ready()
    rows = by_domain(db_path=target, include_imports=include_imports)
    if json_output:
        print(json.dumps([asdict(r) for r in rows], default=str))
        return
    t = Table(show_header=True, title="by domain", title_justify="left")
    t.add_column("domain")
    t.add_column("claims", justify="right")
    t.add_column("permanodes", justify="right")
    t.add_column("bytes", justify="right")
    for r in rows:
        t.add_row(
            r.domain or "(none)",
            f"{r.claim_count:,}",
            f"{r.permanode_count:,}",
            _format_bytes(r.total_bytes),
        )
    console.print(t)


@app.command("extensions")
def extensions_cmd(
    limit: int = typer.Option(20, "--limit", min=1),
    include_imports: bool = typer.Option(
        False, "--include-imports",
        help="Aggregate across attached inventories too (ADR-0013).",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Top ``--limit`` file extensions by total bytes."""
    target = _ensure_db_ready()
    rows = by_extension(        db_path=target, limit=limit, include_imports=include_imports,
    )
    if json_output:
        print(json.dumps([asdict(r) for r in rows], default=str))
        return
    t = Table(show_header=True, title=f"top {limit} extensions", title_justify="left")
    t.add_column("extension")
    t.add_column("claims", justify="right")
    t.add_column("bytes", justify="right")
    for r in rows:
        t.add_row(
            r.extension or "(none)",
            f"{r.claim_count:,}",
            _format_bytes(r.total_bytes),
        )
    console.print(t)


@app.command("classifications")
def classifications_cmd(
    limit: int = typer.Option(20, "--limit", min=1),
    include_imports: bool = typer.Option(
        False, "--include-imports",
        help="Aggregate across attached inventories too (ADR-0013).",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Top ``--limit`` classifications by claim count."""
    target = _ensure_db_ready()
    rows = by_classification(        db_path=target, limit=limit, include_imports=include_imports,
    )
    if json_output:
        print(json.dumps([asdict(r) for r in rows], default=str))
        return
    t = Table(show_header=True, title=f"top {limit} classifications", title_justify="left")
    t.add_column("classification")
    t.add_column("claims", justify="right")
    t.add_column("bytes", justify="right")
    for r in rows:
        t.add_row(
            r.classification or "(none)",
            f"{r.claim_count:,}",
            _format_bytes(r.total_bytes),
        )
    console.print(t)


@app.command("duplicates")
def duplicates_cmd(
    limit: int = typer.Option(20, "--limit", min=1),
    min_claims: int = typer.Option(
        2,
        "--min-claims",
        min=2,
        help="Only surface permanodes with at least this many current claims.",
    ),
    include_imports: bool = typer.Option(
        False, "--include-imports",
        help="Aggregate across attached inventories too (ADR-0013). "
        "Reveals cross-machine duplicates.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Permanodes with the most current claims — the dedup-candidate list."""
    target = _ensure_db_ready()
    rows = duplicate_permanodes(
        db_path=target,        limit=limit,
        min_claims=min_claims,
        include_imports=include_imports,
    )
    if json_output:
        print(json.dumps([asdict(r) for r in rows], default=str))
        return
    t = Table(
        show_header=True,
        title=f"top {limit} duplicates (≥ {min_claims} current claims)",
        title_justify="left",
    )
    t.add_column("permanode")
    t.add_column("canonical_hash")
    t.add_column("size", justify="right")
    t.add_column("current_claims", justify="right")
    for r in rows:
        t.add_row(
            r.permanode_id[:10] + "…",
            r.canonical_hash[:14] + "…",
            _format_bytes(r.size_bytes),
            f"{r.current_claim_count:,}",
        )
    console.print(t)


__all__ = ["app"]
