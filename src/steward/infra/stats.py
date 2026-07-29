# SPDX-License-Identifier: Apache-2.0

"""Read-only aggregations over the inventory.

``steward stats`` lives on top of these aggregators. Each returns a
list of dataclasses that the CLI renders as a Rich table OR a JSON
array. The aggregators are pure SQL — no subprocess, no network.

Six entry points:

* :func:`by_tier` — claim counts + total bytes per tier.
* :func:`by_domain` — same shape, keyed on ``claims.domain``.
* :func:`by_extension` — top-N file extensions.
* :func:`by_classification` — top-N classification labels.
* :func:`duplicate_permanodes` — permanodes with the most current
  claims (dup count).
* :func:`overview` — single dataclass with the headline numbers,
  the top 5 tiers + domains, the largest permanode, the oldest
  scan_run.

All queries run against ``is_current = 1`` claims unless documented
otherwise. The CLI's ``--limit`` flag maps directly to the SQL
``LIMIT`` so caller-supplied bounds prevent surprise on huge
inventories.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from steward.infra.db.connect import connect

# ─────────────────────── value objects ──────────────────────────


@dataclass(frozen=True, slots=True)
class TierStat:
    tier: str
    claim_count: int
    permanode_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class DomainStat:
    domain: str | None  # ``None`` for unclassified
    claim_count: int
    permanode_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class ExtensionStat:
    extension: str | None  # ``None`` for files with no extension
    claim_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class ClassificationStat:
    classification: str | None
    claim_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class DuplicateRow:
    permanode_id: str
    canonical_hash: str
    size_bytes: int
    current_claim_count: int


@dataclass(frozen=True, slots=True)
class OverviewStat:
    permanodes: int
    current_claims: int
    total_bytes: int
    top_tiers: list[TierStat] = field(default_factory=list)
    top_domains: list[DomainStat] = field(default_factory=list)
    largest_permanode: DuplicateRow | None = None
    duplicate_count: int = 0  # permanodes with ≥ 2 current claims


# ─────────────────────── per-axis aggregators ──────────────────────────


def _ro(db_path: Path) -> sqlite3.Connection:
    return connect(db_path, read_only=True, load_vec=False)


def _claims_source_clause(schemas: list[str]) -> str:
    """Build a UNION ALL across schemas for the claims table.

    Returns a parenthesized SQL expression usable as a subquery in
    ``FROM (…) c``. Single-schema callers get the plain table
    reference for query-plan parity with v0.2.13.
    """
    if len(schemas) == 1 and schemas[0] == "":
        return "claims"
    parts = []
    cols = "permanode_id, tier, volume, domain, classification, size_bytes, extension, is_current, observed_at"
    for s in schemas:
        prefix = f"{s}." if s else ""
        parts.append(f"SELECT {cols} FROM {prefix}claims")  # nosec B608
    return "(" + " UNION ALL ".join(parts) + ")"


def _permanodes_source_clause(schemas: list[str]) -> str:
    if len(schemas) == 1 and schemas[0] == "":
        return "permanodes"
    parts = []
    cols = "id, canonical_hash, size_bytes"
    for s in schemas:
        prefix = f"{s}." if s else ""
        parts.append(f"SELECT {cols} FROM {prefix}permanodes")  # nosec B608
    return "(" + " UNION ALL ".join(parts) + ")"


def _schemas_for(db_path: Path, include_imports: bool) -> list[str]:
    """Return the list of schema prefixes to UNION over.

    Single-schema (``[""]``) when ``include_imports=False`` or no
    inventories are attached. Otherwise ``[""] + [attached aliases]``.
    """
    if not include_imports:
        return [""]
    from steward.infra.sync.attach import attach_imports

    # We need the list of attached aliases — open + close a context.
    with attach_imports(db_path=db_path) as ctx:
        return [""] + ctx.aliases


def _run_with_sources(
    db_path: Path,
    *,
    include_imports: bool,
    sql_template: str,
    params: tuple[object, ...] = (),
) -> list[Any]:
    """Execute ``sql_template`` with ``{claims}`` and ``{permanodes}``
    placeholders substituted for the appropriate source clauses.

    On the local-only path the placeholders resolve to plain table
    names (query plan unchanged from v0.2.13). On the fan-out path
    they resolve to parenthesized UNION ALL subqueries.
    """
    if not include_imports:
        sql = sql_template.format(claims="claims", permanodes="permanodes")
        con = _ro(db_path)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    from steward.infra.sync.attach import attach_imports

    with attach_imports(db_path=db_path) as ctx:
        schemas = [""] + ctx.aliases
        sql = sql_template.format(
            claims=_claims_source_clause(schemas),
            permanodes=_permanodes_source_clause(schemas),
        )
        return ctx.connection.execute(sql, params).fetchall()


def by_tier(*, db_path: Path, include_imports: bool = False) -> list[TierStat]:
    """One row per ``claims.tier``, sorted by total_bytes DESC.

    With ``include_imports=True`` (v0.3.6 / ADR-0013) the aggregate
    spans local + every attached inventory's claims.
    """
    if not include_imports:
        # Fast path — preserves v0.2.13 query plan.
        con = _ro(db_path)
        try:
            rows = con.execute(
                """
                SELECT tier,
                       COUNT(*) AS claim_count,
                       COUNT(DISTINCT permanode_id) AS permanode_count,
                       COALESCE(SUM(size_bytes), 0) AS total_bytes
                FROM claims
                WHERE is_current = 1
                GROUP BY tier
                ORDER BY total_bytes DESC, tier ASC
                """
            ).fetchall()
        finally:
            con.close()
        return [
            TierStat(
                tier=str(r[0]),
                claim_count=int(r[1]),
                permanode_count=int(r[2]),
                total_bytes=int(r[3]),
            )
            for r in rows
        ]

    # Fan-out path: UNION ALL across schemas.
    from steward.infra.sync.attach import attach_imports

    with attach_imports(db_path=db_path) as ctx:
        schemas = [""] + ctx.aliases
        source = _claims_source_clause(schemas)
        # source built by _claims_source_clause from controlled allowlist
        sql = (
            "SELECT tier, COUNT(*) AS claim_count, "
            "COUNT(DISTINCT permanode_id) AS permanode_count, "
            "COALESCE(SUM(size_bytes), 0) AS total_bytes "
            f"FROM {source} c "  # nosec B608
            "WHERE is_current = 1 "
            "GROUP BY tier "
            "ORDER BY total_bytes DESC, tier ASC"
        )
        rows = ctx.connection.execute(sql).fetchall()
    return [
        TierStat(
            tier=str(r[0]),
            claim_count=int(r[1]),
            permanode_count=int(r[2]),
            total_bytes=int(r[3]),
        )
        for r in rows
    ]


def by_domain(*, db_path: Path, include_imports: bool = False) -> list[DomainStat]:
    """One row per ``claims.domain`` (NULLs reported as ``None``)."""
    sql = (
        "SELECT domain, COUNT(*) AS claim_count, "
        "COUNT(DISTINCT permanode_id) AS permanode_count, "
        "COALESCE(SUM(size_bytes), 0) AS total_bytes "
        "FROM {claims} c WHERE is_current = 1 GROUP BY domain "
        "ORDER BY total_bytes DESC, domain IS NULL, domain ASC"
    )
    rows = _run_with_sources(db_path, include_imports=include_imports, sql_template=sql)
    return [
        DomainStat(
            domain=(str(r[0]) if r[0] is not None else None),
            claim_count=int(r[1]),
            permanode_count=int(r[2]),
            total_bytes=int(r[3]),
        )
        for r in rows
    ]


def by_extension(*, db_path: Path, limit: int = 20, include_imports: bool = False) -> list[ExtensionStat]:
    """Top-``limit`` file extensions by total bytes."""
    sql = (
        "SELECT extension, COUNT(*) AS claim_count, "
        "COALESCE(SUM(size_bytes), 0) AS total_bytes "
        "FROM {claims} c WHERE is_current = 1 GROUP BY extension "
        "ORDER BY total_bytes DESC, extension IS NULL, extension ASC LIMIT ?"
    )
    rows = _run_with_sources(
        db_path,
        include_imports=include_imports,
        sql_template=sql,
        params=(int(limit),),
    )
    return [
        ExtensionStat(
            extension=(str(r[0]) if r[0] is not None else None),
            claim_count=int(r[1]),
            total_bytes=int(r[2]),
        )
        for r in rows
    ]


def by_classification(*, db_path: Path, limit: int = 20, include_imports: bool = False) -> list[ClassificationStat]:
    """Top-``limit`` classifications by claim count."""
    sql = (
        "SELECT classification, COUNT(*) AS claim_count, "
        "COALESCE(SUM(size_bytes), 0) AS total_bytes "
        "FROM {claims} c WHERE is_current = 1 GROUP BY classification "
        "ORDER BY claim_count DESC, classification IS NULL, classification ASC LIMIT ?"
    )
    rows = _run_with_sources(
        db_path,
        include_imports=include_imports,
        sql_template=sql,
        params=(int(limit),),
    )
    return [
        ClassificationStat(
            classification=(str(r[0]) if r[0] is not None else None),
            claim_count=int(r[1]),
            total_bytes=int(r[2]),
        )
        for r in rows
    ]


def duplicate_permanodes(
    *,
    db_path: Path,
    limit: int = 20,
    min_claims: int = 2,
    include_imports: bool = False,
) -> list[DuplicateRow]:
    """Permanodes with the most current claims — the dedup-candidate list.

    ``min_claims`` filters out singletons (the default of 2 surfaces
    only permanodes that have at least one duplicate). Cross-machine
    duplicates surface naturally when ``include_imports=True``.
    """
    # Using COUNT(*) instead of COUNT(c.id): the UNION-ALL source
    # for claims doesn't project ``id`` (and per-schema id values
    # would collide anyway). COUNT(*) is semantically equivalent on
    # this inner join.
    sql = (
        "SELECT p.id, p.canonical_hash, p.size_bytes, "
        "COUNT(*) AS current_claim_count "
        "FROM {permanodes} p "
        "JOIN {claims} c ON c.permanode_id = p.id AND c.is_current = 1 "
        "GROUP BY p.id, p.canonical_hash, p.size_bytes "
        "HAVING current_claim_count >= ? "
        "ORDER BY current_claim_count DESC, p.size_bytes DESC LIMIT ?"
    )
    rows = _run_with_sources(
        db_path,
        include_imports=include_imports,
        sql_template=sql,
        params=(int(min_claims), int(limit)),
    )
    return [
        DuplicateRow(
            permanode_id=str(r[0]),
            canonical_hash=str(r[1]),
            size_bytes=int(r[2]),
            current_claim_count=int(r[3]),
        )
        for r in rows
    ]


def overview(*, db_path: Path, top_n: int = 5, include_imports: bool = False) -> OverviewStat:
    """Single headline aggregate. Drives ``steward stats`` (no args)."""
    head_sql = (
        "SELECT "
        "(SELECT COUNT(*) FROM {permanodes} p), "
        "(SELECT COUNT(*) FROM {claims} c WHERE is_current = 1), "
        "(SELECT COALESCE(SUM(size_bytes), 0) FROM {claims} c WHERE is_current = 1)"
    )
    head_rows = _run_with_sources(db_path, include_imports=include_imports, sql_template=head_sql)
    head = head_rows[0] if head_rows else (0, 0, 0)
    permanodes = int(head[0] or 0)
    current_claims = int(head[1] or 0)
    total_bytes = int(head[2] or 0)

    largest_sql = (
        "SELECT p.id, p.canonical_hash, p.size_bytes, "
        "(SELECT COUNT(*) FROM {claims} c WHERE c.permanode_id = p.id AND c.is_current = 1) "
        "FROM {permanodes} p "
        "WHERE EXISTS (SELECT 1 FROM {claims} c WHERE c.permanode_id = p.id AND c.is_current = 1) "
        "ORDER BY p.size_bytes DESC LIMIT 1"
    )
    largest_rows = _run_with_sources(db_path, include_imports=include_imports, sql_template=largest_sql)
    largest = (
        DuplicateRow(
            permanode_id=str(largest_rows[0][0]),
            canonical_hash=str(largest_rows[0][1]),
            size_bytes=int(largest_rows[0][2]),
            current_claim_count=int(largest_rows[0][3] or 0),
        )
        if largest_rows
        else None
    )

    dup_sql = (
        "SELECT COUNT(*) FROM ("
        "SELECT permanode_id FROM {claims} c WHERE is_current = 1 "
        "GROUP BY permanode_id HAVING COUNT(*) >= 2)"
    )
    dup_rows = _run_with_sources(db_path, include_imports=include_imports, sql_template=dup_sql)
    dup_count = int(dup_rows[0][0] or 0) if dup_rows else 0

    return OverviewStat(
        permanodes=permanodes,
        current_claims=current_claims,
        total_bytes=total_bytes,
        top_tiers=by_tier(db_path=db_path, include_imports=include_imports)[:top_n],
        top_domains=by_domain(db_path=db_path, include_imports=include_imports)[:top_n],
        largest_permanode=largest,
        duplicate_count=dup_count,
    )


__all__ = [
    "ClassificationStat",
    "DomainStat",
    "DuplicateRow",
    "ExtensionStat",
    "OverviewStat",
    "TierStat",
    "by_classification",
    "by_domain",
    "by_extension",
    "by_tier",
    "duplicate_permanodes",
    "overview",
]
