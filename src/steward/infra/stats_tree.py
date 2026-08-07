# SPDX-License-Identifier: Apache-2.0
"""Lazy path-tree aggregation for inventory surface (ADR-0022).

Depth-1 children are aggregated **in SQL** (GROUP BY next path segment)
so multi‑GB inventories do not undercount under a raw-row fetch cap.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from steward.core.matrix.path_segments import child_path, normalize_prefix
from steward.core.matrix.types import PathTreeNode, PathTreeRequest, PathTreeResult
from steward.core.matrix.validate import validate_path_tree
from steward.infra.db.connect import connect


@dataclass
class _Agg:
    claim_count: int = 0
    permanode_count: int = 0
    total_bytes: int = 0
    is_dir: bool = False
    overlay_bytes: dict[str, int] = field(default_factory=dict)

    def add_sql_row(
        self,
        *,
        claim_count: int,
        permanode_count: int,
        total_bytes: int,
        is_dir: bool,
        overlay_key: str | None,
    ) -> None:
        self.claim_count += claim_count
        self.permanode_count += permanode_count
        self.total_bytes += total_bytes
        self.is_dir = self.is_dir or is_dir
        if overlay_key:
            self.overlay_bytes[overlay_key] = self.overlay_bytes.get(overlay_key, 0) + total_bytes

    def dominant_overlay(self) -> str | None:
        if not self.overlay_bytes:
            return None
        return max(self.overlay_bytes.items(), key=lambda kv: (kv[1], kv[0]))[0]


def _seg_sql(prefix: str) -> str:
    """SQL expression for the next path segment (or leaf name) under prefix."""
    if prefix:
        rest = f"substr(file_path, {len(prefix) + 2})"
        return (
            f"CASE "
            f"WHEN {rest} = '' OR {rest} IS NULL THEN NULL "
            f"WHEN instr({rest}, '/') = 0 THEN {rest} "
            f"ELSE substr({rest}, 1, instr({rest}, '/') - 1) "
            f"END"
        )
    rest = "ltrim(file_path, '/')"
    return (
        f"CASE "
        f"WHEN {rest} = '' OR {rest} IS NULL THEN NULL "
        f"WHEN instr({rest}, '/') = 0 THEN {rest} "
        f"ELSE substr({rest}, 1, instr({rest}, '/') - 1) "
        f"END"
    )


def _is_dir_sql(prefix: str) -> str:
    if prefix:
        rest = f"substr(file_path, {len(prefix) + 2})"
    else:
        rest = "ltrim(file_path, '/')"
    return f"CASE WHEN instr({rest}, '/') > 0 THEN 1 ELSE 0 END"


def _overlay_sql(color_by: str, *, has_source_col: bool) -> str | None:
    if color_by == "none":
        return None
    if color_by == "domain":
        return "COALESCE(domain, '(none)')"
    if color_by == "extension":
        return "COALESCE(extension, '(none)')"
    if color_by == "tier":
        return "COALESCE(tier, '(none)')"
    if color_by == "source":
        return "source" if has_source_col else "'local'"
    return None


def _where_and_params(
    prefix: str,
    tier: str | None,
    volume: str | None,
) -> tuple[str, list[object], list[str]]:
    notes: list[str] = []
    where = ["is_current = 1"]
    params: list[object] = []
    if prefix:
        where.append("(file_path = ? OR file_path LIKE ?)")
        params.extend([prefix, prefix + "/%"])
    if tier:
        where.append("tier = ?")
        params.append(tier)
    if volume:
        where.append("volume = ?")
        params.append(volume)
    if not prefix and not tier and not volume:
        notes.append("unscoped path tree: apply tier/volume/prefix for large inventories")
    return " AND ".join(where), params, notes


def _build_sql(
    *,
    prefix: str,
    where_sql: str,
    color_by: str,
    measure: str,
    child_limit: int,
    has_source_col: bool,
) -> tuple[str, bool]:
    """Return (sql, uses_overlay_groups). Placeholders: {source}; params append limit when not overlay."""
    seg_expr = _seg_sql(prefix)
    is_dir_expr = _is_dir_sql(prefix)
    overlay_expr = _overlay_sql(color_by, has_source_col=has_source_col)
    order = {
        "claim_count": "claim_count",
        "permanode_count": "permanode_count",
        "total_bytes": "total_bytes",
    }.get(measure, "total_bytes")

    if overlay_expr is None:
        sql = (
            f"SELECT {seg_expr} AS seg, "
            f"COUNT(*) AS claim_count, "
            f"COUNT(DISTINCT permanode_id) AS permanode_count, "
            f"COALESCE(SUM(size_bytes), 0) AS total_bytes, "
            f"MAX({is_dir_expr}) AS is_dir "
            f"FROM {{source}} c WHERE {where_sql} "  # nosec B608
            f"AND ({seg_expr}) IS NOT NULL "
            f"GROUP BY seg "
            f"ORDER BY {order} DESC, seg ASC "
            f"LIMIT ?"
        )
        return sql, False

    sql = (
        f"SELECT {seg_expr} AS seg, "
        f"{overlay_expr} AS ov, "
        f"COUNT(*) AS claim_count, "
        f"COUNT(DISTINCT permanode_id) AS permanode_count, "
        f"COALESCE(SUM(size_bytes), 0) AS total_bytes, "
        f"MAX({is_dir_expr}) AS is_dir "
        f"FROM {{source}} c WHERE {where_sql} "  # nosec B608
        f"AND ({seg_expr}) IS NOT NULL "
        f"GROUP BY seg, ov "
        f"ORDER BY total_bytes DESC, seg ASC"
    )
    return sql, True


def path_tree_depth1(*, db_path: Path, req: PathTreeRequest) -> PathTreeResult:
    """Aggregate next path segment (and direct leaves) under path_prefix."""
    req = validate_path_tree(req)
    prefix = normalize_prefix(req.path_prefix)
    where_sql, params, notes = _where_and_params(prefix, req.tier, req.volume)
    child_limit = int(req.child_limit)

    def run(source: str, con: sqlite3.Connection, *, has_source_col: bool) -> tuple[list[Any], bool]:
        sql_tmpl, uses_overlay = _build_sql(
            prefix=prefix,
            where_sql=where_sql,
            color_by=req.color_by,
            measure=req.measure,
            child_limit=child_limit,
            has_source_col=has_source_col,
        )
        sql = sql_tmpl.format(source=source)
        if uses_overlay:
            return list(con.execute(sql, tuple(params)).fetchall()), True
        return list(con.execute(sql, (*params, child_limit + 1)).fetchall()), False

    if not req.include_imports:
        con = connect(db_path, read_only=True, load_vec=False)
        try:
            rows, uses_overlay = run("claims", con, has_source_col=False)
        finally:
            con.close()
    else:
        from steward.infra.stats_matrix import _claims_source_with_source
        from steward.infra.sync.attach import attach_imports

        with attach_imports(db_path=db_path) as ctx:
            schemas = [""] + ctx.aliases
            source = _claims_source_with_source(schemas)
            rows, uses_overlay = run(source, ctx.connection, has_source_col=True)
            if not ctx.aliases:
                notes.append("include_imports=true but no attached inventories")

    buckets: dict[str, _Agg] = {}
    if not uses_overlay:
        truncated = len(rows) > child_limit
        if truncated:
            rows = rows[:child_limit]
        for r in rows:
            name = str(r[0])
            buckets[name] = _Agg(
                claim_count=int(r[1]),
                permanode_count=int(r[2]),
                total_bytes=int(r[3]),
                is_dir=bool(r[4]),
            )
    else:
        for r in rows:
            name = str(r[0])
            ov = str(r[1]) if r[1] is not None else "(none)"
            if name not in buckets:
                buckets[name] = _Agg()
            buckets[name].add_sql_row(
                claim_count=int(r[2]),
                permanode_count=int(r[3]),
                total_bytes=int(r[4]),
                is_dir=bool(r[5]),
                overlay_key=ov,
            )
        truncated = len(buckets) > child_limit

    def sort_key(item: tuple[str, _Agg]) -> tuple[int, str]:
        name, agg = item
        if req.measure == "claim_count":
            return (-agg.claim_count, name)
        if req.measure == "permanode_count":
            return (-agg.permanode_count, name)
        return (-agg.total_bytes, name)

    ordered = sorted(buckets.items(), key=sort_key)[:child_limit]

    children = tuple(
        PathTreeNode(
            path=child_path(prefix, name),
            name=name,
            is_dir=agg.is_dir,
            claim_count=agg.claim_count,
            permanode_count=agg.permanode_count,
            total_bytes=agg.total_bytes,
            overlay_value=agg.dominant_overlay() if req.color_by != "none" else None,
        )
        for name, agg in ordered
    )

    return PathTreeResult(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        path_prefix=prefix,
        measure=req.measure,
        color_by=req.color_by,
        children=children,
        truncated=truncated,
        include_imports=req.include_imports,
        notes=tuple(notes),
    )


def path_tree_to_dict(result: PathTreeResult) -> dict[str, Any]:
    return asdict(result)
