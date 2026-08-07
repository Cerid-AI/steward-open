# SPDX-License-Identifier: Apache-2.0
"""Multi-dimensional claim aggregations (ADR-0022 data matrix)."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from steward.core.matrix.types import (
    DIMENSION_COLUMNS,
    CrossStatsCell,
    CrossStatsRequest,
    CrossStatsResult,
)
from steward.core.matrix.validate import validate_cross
from steward.infra.db.connect import connect

_CLAIM_COLS = (
    "permanode_id, tier, volume, domain, classification, size_bytes, "
    "extension, is_current, observed_at, machine_id, file_path"
)


def _ro(db_path: Path) -> sqlite3.Connection:
    return connect(db_path, read_only=True, load_vec=False)


def _claims_source_with_source(schemas: list[str]) -> str:
    """UNION ALL of claims projecting a ``source`` column (local|attached)."""
    parts: list[str] = []
    for s in schemas:
        prefix = f"{s}." if s else ""
        label = "local" if not s else "attached"
        parts.append(
            f"SELECT {_CLAIM_COLS}, '{label}' AS source FROM {prefix}claims"  # nosec B608
        )
    return "(" + " UNION ALL ".join(parts) + ")"


def _claims_source_plain(schemas: list[str]) -> str:
    if len(schemas) == 1 and schemas[0] == "":
        return "claims"
    parts = []
    for s in schemas:
        prefix = f"{s}." if s else ""
        parts.append(f"SELECT {_CLAIM_COLS} FROM {prefix}claims")  # nosec B608
    return "(" + " UNION ALL ".join(parts) + ")"


def _dim_expr(dim: str, *, has_source_col: bool) -> str:
    if dim == "source":
        return "source" if has_source_col else "'local'"
    col = DIMENSION_COLUMNS.get(dim)
    if col is None:
        raise ValueError(f"unsupported dimension: {dim}")
    return col


def _order_expr(measure: str) -> str:
    if measure == "claim_count":
        return "claim_count"
    if measure == "permanode_count":
        return "permanode_count"
    return "total_bytes"


def cross_stats(*, db_path: Path, req: CrossStatsRequest) -> CrossStatsResult:
    """Aggregate current claims by one or two dimensions."""
    req = validate_cross(req)
    notes: list[str] = []
    needs_source = (
        req.dim_a == "source" or req.dim_b == "source" or "source" in req.filters or req.include_imports
    )

    where = ["is_current = 1"]
    params: list[object] = []

    if req.path_prefix:
        pref = req.path_prefix.rstrip("/")
        where.append("(file_path = ? OR file_path LIKE ?)")
        params.extend([pref, pref + "/%"])

    for key, value in req.filters.items():
        if key == "path_prefix":
            continue
        if key == "source":
            where.append("source = ?")
            params.append(value)
            continue
        col = DIMENSION_COLUMNS.get(key)
        if col is None:
            continue
        where.append(f"{col} = ?")
        params.append(value)

    where_sql = " AND ".join(where)
    order = _order_expr(req.measure)
    limit = int(req.limit)

    def execute(schemas: list[str], con: sqlite3.Connection) -> list[Sequence[Any]]:
        has_source_col = needs_source
        if has_source_col:
            source = _claims_source_with_source(schemas)
        else:
            source = _claims_source_plain(schemas)
        a_expr = _dim_expr(req.dim_a, has_source_col=has_source_col)
        if req.dim_b is None:
            sql = (
                f"SELECT {a_expr} AS a, NULL AS b, "
                f"COUNT(*) AS claim_count, "
                f"COUNT(DISTINCT permanode_id) AS permanode_count, "
                f"COALESCE(SUM(size_bytes), 0) AS total_bytes "
                f"FROM {source} c WHERE {where_sql} "  # nosec B608
                f"GROUP BY a "
                f"ORDER BY {order} DESC, a IS NULL, a ASC "
                f"LIMIT ?"
            )
        else:
            b_expr = _dim_expr(req.dim_b, has_source_col=has_source_col)
            sql = (
                f"SELECT {a_expr} AS a, {b_expr} AS b, "
                f"COUNT(*) AS claim_count, "
                f"COUNT(DISTINCT permanode_id) AS permanode_count, "
                f"COALESCE(SUM(size_bytes), 0) AS total_bytes "
                f"FROM {source} c WHERE {where_sql} "  # nosec B608
                f"GROUP BY a, b "
                f"ORDER BY {order} DESC, a IS NULL, a ASC, b IS NULL, b ASC "
                f"LIMIT ?"
            )
        return list(con.execute(sql, (*params, limit + 1)).fetchall())

    if not req.include_imports:
        con = _ro(db_path)
        try:
            rows = execute([""], con)
        finally:
            con.close()
    else:
        from steward.infra.sync.attach import attach_imports

        with attach_imports(db_path=db_path) as ctx:
            schemas = [""] + ctx.aliases
            rows = execute(schemas, ctx.connection)
            if not ctx.aliases:
                notes.append("include_imports=true but no attached inventories")

    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]

    cells = tuple(
        CrossStatsCell(
            a=(str(r[0]) if r[0] is not None else None),
            b=(str(r[1]) if r[1] is not None else None),
            claim_count=int(r[2]),
            permanode_count=int(r[3]),
            total_bytes=int(r[4]),
        )
        for r in rows
    )
    return CrossStatsResult(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        dim_a=req.dim_a,
        dim_b=req.dim_b,
        measure=req.measure,
        cells=cells,
        truncated=truncated,
        include_imports=req.include_imports,
        path_prefix=req.path_prefix,
        notes=tuple(notes),
    )


def cross_stats_to_dict(result: CrossStatsResult) -> dict[str, Any]:
    return asdict(result)
