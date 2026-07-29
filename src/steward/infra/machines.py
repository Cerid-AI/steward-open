# SPDX-License-Identifier: Apache-2.0

"""Multi-machine awareness — surface the ``machine_id`` column.

Every claim, scan_run, and audit_log row carries a ``machine_id`` since
v0.1.0 (ADR-0008 said this column would exist from day one so a v0.3+
multi-machine activation wouldn't require rewriting the audit chain).
This module is that activation: it aggregates over the existing tables
to answer "which machines have ever touched this inventory, and what
did each of them do."

Two value objects:

* :class:`MachineSummary` — one row per ``machine_id``: counts of
  claims / scan_runs / audit entries + first / last seen timestamps.
* :class:`MachineDetails` — extends :class:`MachineSummary` with
  recent activity (top N scan_runs + top N audit actions).

Three aggregator entry points:

* :func:`list_machines(db_path)` — every machine, sorted by last_seen
  descending.
* :func:`get_machine(db_path, machine_id)` — full details for one
  machine, or ``None`` when nothing in the inventory references it.
* :func:`count_machines(db_path)` — cheap count for the status report.

All three accept an opt-in ``include_imports`` flag (v0.3.5+). When
True the aggregator UNION-ALLs across attached inventory schemas
(per ADR-0013), so an operator who has imported other machines'
inventories sees their rows alongside local ones. Default is local-only
to preserve the v0.1/v0.2 surface unchanged.

Aggregators are pure SQL — no subprocess, no network. ``read_only``
connections everywhere so even a coding bug can't mutate state.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from steward.infra.db.connect import connect


@dataclass(frozen=True, slots=True)
class MachineSummary:
    """One row per ``machine_id``.

    Attributes
    ----------
    machine_id:
        The UUID Steward assigns to a host on first ``db migrate``.
    is_current:
        True iff this machine matches the ``meta.machine_id`` value of
        the inventory being queried — i.e. "this is my machine."
    claim_count:
        Total claims attributed to this machine (current + historical).
    current_claim_count:
        Claims with ``is_current = 1``.
    scan_run_count:
        Scan_run rows started by this machine.
    audit_entry_count:
        Audit-log rows written by this machine.
    first_seen_at / last_seen_at:
        Earliest / latest audit_log timestamp for this machine.
        ``None`` when the machine has no audit rows (theoretically
        impossible after the first scan, but the aggregator is
        defensive).
    """

    machine_id: str
    is_current: bool
    claim_count: int
    current_claim_count: int
    scan_run_count: int
    audit_entry_count: int
    first_seen_at: str | None
    last_seen_at: str | None
    source: str = "local"
    """Where this machine's rows live: ``"local"`` for the host's own
    inventory, ``"attached"`` for any row drawn from an imported
    schema (ADR-0013). On a single-machine install every row is
    ``"local"``."""


@dataclass(frozen=True, slots=True)
class MachineActivity:
    """One row of activity (most-recent scan_run or audit entry)."""

    kind: str  # "scan_run" | "audit"
    timestamp: str
    summary: str


@dataclass(frozen=True, slots=True)
class MachineDetails:
    """Summary + recent activity for one machine."""

    summary: MachineSummary
    recent_scan_runs: list[MachineActivity] = field(default_factory=list)
    recent_audit: list[MachineActivity] = field(default_factory=list)


# ─────────────────────── helpers ──────────────────────────


def _current_machine_id(con: sqlite3.Connection) -> str | None:
    """Read ``meta.machine_id`` — the UUID assigned to this host."""
    row = con.execute("SELECT value FROM meta WHERE key = 'machine_id'").fetchone()
    return str(row[0]) if row is not None else None


def _union_clause(table: str, columns: str, schemas: list[str]) -> str:
    """Build a ``SELECT … FROM <schema>.<table>`` UNION ALL across schemas.

    ``schemas`` should include the empty string for the ``main`` (local)
    schema; non-empty entries are attached aliases (e.g. ``m_abc123``).
    """
    parts = []
    for s in schemas:
        prefix = f"{s}." if s else ""
        # nosec B608 — column list is static; prefix is from controlled allowlist
        parts.append(f"SELECT {columns} FROM {prefix}{table}")  # nosec B608
    return "\nUNION ALL\n".join(parts)


def _machine_summary_rows(
    con: sqlite3.Connection,
    *,
    schemas: list[str] | None = None,
) -> list[tuple[str, int, int, int, int, str | None, str | None]]:
    """Run the aggregate query that powers :class:`MachineSummary`.

    Computed via three left joins on the ``machine_id`` axis. SQLite
    handles this in milliseconds even at the millions-of-rows scale
    Steward targets.

    The set of machine_ids comes from a UNION across all three tables
    so a machine that only ever wrote audit rows (e.g. a one-off
    integrity check) still surfaces, even if no claims or scan_runs
    landed for it.

    When ``schemas`` is provided (a non-empty list including ``""``
    for the local schema and attached aliases like ``"m_abc123"``),
    each source table is itself a UNION ALL across those schemas
    before the aggregation runs.
    """
    if schemas is None:
        schemas = [""]
    claims_union = _union_clause(
        "claims",
        "machine_id, is_current",
        schemas,
    )
    scan_runs_union = _union_clause("scan_runs", "machine_id", schemas)
    audit_union = _union_clause(
        "audit_log",
        "machine_id, timestamp",
        schemas,
    )
    # When fan-out is active (more than just the local schema), pull
    # machine_ids from ``attached_inventories`` as well — an imported
    # peer with zero scan / audit activity still belongs on the
    # "known machines" list. Single-schema callers skip this branch
    # to keep the v0.2.9 query plan unchanged.
    attached_clause = "UNION SELECT DISTINCT machine_id FROM attached_inventories" if len(schemas) > 1 else ""

    # Every dynamic insertion is built by _union_clause from an
    # allowlist of schema names (the local "main" + attached schema
    # aliases derived from validated UUIDs). No user input is
    # interpolated — bandit-safe.
    sql = (
        "WITH "
        f"claims_all AS ({claims_union}), "  # nosec B608
        f"scan_runs_all AS ({scan_runs_union}), "  # nosec B608
        f"audit_all AS ({audit_union}), "  # nosec B608
        "all_machines AS ("
        "  SELECT DISTINCT machine_id FROM claims_all"
        "  UNION SELECT DISTINCT machine_id FROM scan_runs_all"
        "  UNION SELECT DISTINCT machine_id FROM audit_all "
        f"  {attached_clause}"
        "), "
        "claim_totals AS ("
        "  SELECT machine_id,"
        "  COUNT(*) AS total,"
        "  SUM(CASE WHEN is_current = 1 THEN 1 ELSE 0 END) AS current_total"
        "  FROM claims_all GROUP BY machine_id"
        "), "
        "scan_run_totals AS ("
        "  SELECT machine_id, COUNT(*) AS total FROM scan_runs_all GROUP BY machine_id"
        "), "
        "audit_totals AS ("
        "  SELECT machine_id,"
        "  COUNT(*) AS total,"
        "  MIN(timestamp) AS first_seen,"
        "  MAX(timestamp) AS last_seen"
        "  FROM audit_all GROUP BY machine_id"
        ") "
        "SELECT m.machine_id, "
        "  COALESCE(c.total, 0), "
        "  COALESCE(c.current_total, 0), "
        "  COALESCE(s.total, 0), "
        "  COALESCE(a.total, 0), "
        "  a.first_seen, a.last_seen "
        "FROM all_machines m "
        "LEFT JOIN claim_totals    c ON c.machine_id = m.machine_id "
        "LEFT JOIN scan_run_totals s ON s.machine_id = m.machine_id "
        "LEFT JOIN audit_totals    a ON a.machine_id = m.machine_id "
        "ORDER BY COALESCE(a.last_seen, '') DESC, m.machine_id ASC"
    )
    rows = con.execute(sql).fetchall()
    return [
        (
            str(r[0]),
            int(r[1] or 0),
            int(r[2] or 0),
            int(r[3] or 0),
            int(r[4] or 0),
            (str(r[5]) if r[5] is not None else None),
            (str(r[6]) if r[6] is not None else None),
        )
        for r in rows
    ]


# ─────────────────────── public surface ──────────────────────────


def list_machines(
    *,
    db_path: Path,
    include_imports: bool = False,
) -> list[MachineSummary]:
    """Return every machine that has ever appeared in claims /
    scan_runs / audit, sorted by ``last_seen_at`` descending.

    Default behaviour is local-only — preserves the v0.2.9 surface.
    With ``include_imports=True``, attached inventories' machine_ids
    show up alongside, each tagged ``source="attached"`` so the
    caller can render the distinction.
    """
    if not include_imports:
        # Fast path: single-schema aggregation.
        con = connect(db_path, read_only=True, load_vec=False)
        try:
            current_id = _current_machine_id(con)
            rows = _machine_summary_rows(con, schemas=[""])
        finally:
            con.close()
        return [
            MachineSummary(
                machine_id=mid,
                is_current=(current_id is not None and mid == current_id),
                claim_count=claims,
                current_claim_count=current_claims,
                scan_run_count=scan_runs,
                audit_entry_count=audits,
                first_seen_at=first,
                last_seen_at=last,
                source="local",
            )
            for (mid, claims, current_claims, scan_runs, audits, first, last) in rows
        ]

    # Fan-out: aggregate across local + every attached schema.
    from steward.infra.sync.attach import attach_imports

    with attach_imports(db_path=db_path) as ctx:
        current_id = _current_machine_id(ctx.connection)
        schemas = [""] + ctx.aliases
        rows = _machine_summary_rows(ctx.connection, schemas=schemas)

        # Per-machine: which schema did it actually appear in? We
        # check each candidate alias in order; the FIRST match
        # determines the source. (A single machine_id only ever
        # appears in one schema in practice — local writes its own
        # rows to main, attached schemas hold OTHER machines' rows.)
        sources: dict[str, str] = {}
        alias_to_machine = {s.alias: s.machine_id for s in ctx.attached}
        # Determine source by querying main vs each attached.
        for mid, _claims, _cur, _sr, _au, _first, _last in rows:
            local_hit = ctx.connection.execute(
                "SELECT 1 FROM claims WHERE machine_id = ? "
                "UNION SELECT 1 FROM scan_runs WHERE machine_id = ? "
                "UNION SELECT 1 FROM audit_log WHERE machine_id = ? "
                "LIMIT 1",
                (mid, mid, mid),
            ).fetchone()
            if local_hit is not None:
                sources[mid] = "local"
                continue
            # Otherwise the machine_id must come from an attached
            # schema — by ADR-0013 each attached inventory's
            # machine_id is its own exporter's id, so this is fast
            # in practice (no fan-out probe needed).
            sources[mid] = "attached"
        # Override: the attached schema's exporter machine_id is the
        # alias-to-machine map's value. If the row's machine_id matches
        # an alias_to_machine entry, we know its origin schema.
        for _alias, attached_mid in alias_to_machine.items():
            sources.setdefault(attached_mid, "attached")

    return [
        MachineSummary(
            machine_id=mid,
            is_current=(current_id is not None and mid == current_id),
            claim_count=claims,
            current_claim_count=current_claims,
            scan_run_count=scan_runs,
            audit_entry_count=audits,
            first_seen_at=first,
            last_seen_at=last,
            source=sources.get(mid, "local"),
        )
        for (mid, claims, current_claims, scan_runs, audits, first, last) in rows
    ]


def count_machines(*, db_path: Path, include_imports: bool = False) -> int:
    """Cheap row-count of distinct machine_ids — used by the status report.

    With ``include_imports=True``, attached inventories' machine_ids
    are counted too.
    """
    if not include_imports:
        con = connect(db_path, read_only=True, load_vec=False)
        try:
            row = con.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT machine_id FROM claims
                    UNION SELECT machine_id FROM scan_runs
                    UNION SELECT machine_id FROM audit_log
                )
                """
            ).fetchone()
        finally:
            con.close()
        return int(row[0] or 0) if row is not None else 0

    return len(list_machines(db_path=db_path, include_imports=True))


def get_machine(
    *,
    db_path: Path,
    machine_id: str,
    include_imports: bool = False,
) -> MachineDetails | None:
    """Return :class:`MachineDetails` for ``machine_id``, or ``None``
    when no row in any inspected schema references it.

    With ``include_imports=True``, also inspects attached schemas.
    If the machine_id is found only in an attached schema, recent
    scan_runs + audit rows come from that schema (read-only).
    """
    summaries = list_machines(db_path=db_path, include_imports=include_imports)
    target = next((s for s in summaries if s.machine_id == machine_id), None)
    if target is None:
        return None

    # Pick the right schema for recent-activity queries. Local first;
    # if include_imports and source=="attached" we route the queries
    # to the matching attached schema.
    if not include_imports or target.source == "local":
        con = connect(db_path, read_only=True, load_vec=False)
        schema_prefix = ""
        try:
            recent_scans, recent_audit = _fetch_recent_for_machine(con, machine_id, schema_prefix)
        finally:
            con.close()
        return MachineDetails(
            summary=target,
            recent_scan_runs=recent_scans,
            recent_audit=recent_audit,
        )

    # Attached path — find the matching alias.
    from steward.infra.sync.attach import attach_imports

    with attach_imports(db_path=db_path) as ctx:
        alias = next(
            (s.alias for s in ctx.attached if s.machine_id == machine_id),
            None,
        )
        if alias is None:
            # Machine was on the summary list (its row came from somewhere)
            # but the matching attached schema isn't currently mountable.
            return MachineDetails(summary=target)
        recent_scans, recent_audit = _fetch_recent_for_machine(ctx.connection, machine_id, f"{alias}.")

    return MachineDetails(
        summary=target,
        recent_scan_runs=recent_scans,
        recent_audit=recent_audit,
    )


def _fetch_recent_for_machine(
    con: sqlite3.Connection,
    machine_id: str,
    schema_prefix: str,
) -> tuple[list[MachineActivity], list[MachineActivity]]:
    """Pull recent scan_runs + audit rows for one machine from one
    schema (``"main."`` for local, or ``"<alias>."`` for attached).

    Both queries take the same ``machine_id`` bind and apply the
    same LIMIT as the v0.2.9 single-schema version.
    """
    scans_sql = (
        f"SELECT id, COALESCE(finished_at, started_at), root_path, "
        f"files_hashed, errors "
        f"FROM {schema_prefix}scan_runs "  # nosec B608 — prefix from controlled allowlist
        f"WHERE machine_id = ? ORDER BY started_at DESC LIMIT 5"
    )
    audit_sql = (
        f"SELECT id, timestamp, action, actor "
        f"FROM {schema_prefix}audit_log "  # nosec B608 — prefix from controlled allowlist
        f"WHERE machine_id = ? ORDER BY id DESC LIMIT 10"
    )
    recent_scans = [
        MachineActivity(
            kind="scan_run",
            timestamp=str(r[1]),
            summary=(f"scan_run_id={int(r[0])} root={r[2]!s} hashed={int(r[3] or 0)} errors={int(r[4] or 0)}"),
        )
        for r in con.execute(scans_sql, (machine_id,))
    ]
    recent_audit = [
        MachineActivity(
            kind="audit",
            timestamp=str(r[1]),
            summary=f"{r[2]} (actor={r[3]})",
        )
        for r in con.execute(audit_sql, (machine_id,))
    ]
    return (recent_scans, recent_audit)


__all__ = [
    "MachineActivity",
    "MachineDetails",
    "MachineSummary",
    "count_machines",
    "get_machine",
    "list_machines",
]
