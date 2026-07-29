# SPDX-License-Identifier: Apache-2.0

"""Operator dashboard — read-only aggregators that summarise inventory state.

Single-pane status query that pulls together:

* **Inventory** — counts (permanodes, current claims, scan_runs,
  audit_log) + the inventory.db's size + last-modified time.
* **Latest scan** — when, how long, errors.
* **Stash** — pending entries grouped by run_id, oldest cooling-off
  date.
* **Last replicate** — most-recent `replicate_end` audit row, with
  totals.
* **Last archive** — most-recent `archive_end` audit row, with totals.
* **Audit chain** — result of :func:`verify_chain` so the operator
  knows the chain is intact.

Everything is computed off the audit_log + a handful of read-only
SQL aggregates. No subprocesses, no network calls. ``steward status``
is intended to be cheap to run on demand.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from steward.infra.db.admin import verify_chain
from steward.infra.db.connect import connect
from steward.infra.machines import count_machines


@dataclass(frozen=True, slots=True)
class InventoryCounts:
    permanodes: int
    current_claims: int
    scan_runs: int
    audit_entries: int
    machines: int


@dataclass(frozen=True, slots=True)
class DbFileInfo:
    path: str
    size_bytes: int
    modified_iso: str | None


@dataclass(frozen=True, slots=True)
class LatestScan:
    """Most-recent finished scan_run. All fields None when there's no
    finished run yet."""

    scan_run_id: int | None
    root_path: str | None
    started_at: str | None
    finished_at: str | None
    files_walked: int = 0
    files_hashed: int = 0
    files_skipped: int = 0
    bytes_hashed: int = 0
    errors: int = 0


@dataclass(frozen=True, slots=True)
class StashSummary:
    """Aggregate over currently-in-flight stash entries."""

    in_flight_entries: int
    distinct_run_ids: int
    oldest_ts_iso: str | None
    newest_ts_iso: str | None


@dataclass(frozen=True, slots=True)
class LatestAdapterRun:
    """Most-recent ``*_end`` audit row for a single adapter (replicate /
    archive). All fields None when no such row exists."""

    action: str
    timestamp: str | None
    policy_name: str | None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuditChainStatus:
    rows_checked: int
    ok: bool
    error: str | None
    skipped: bool = False
    """True when ``--quick`` skipped the full chain walk."""


@dataclass(frozen=True, slots=True)
class RollupInfo:
    """Whether inventory counts came from meta cache."""

    used_cache: bool
    refreshed_at: str | None
    max_age_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class StatusReport:
    """Top-level container assembled by :func:`collect_status`.

    Convert to a JSON-friendly dict via :func:`status_to_dict` for the
    ``--json`` CLI flag.
    """

    db: DbFileInfo
    inventory: InventoryCounts
    latest_scan: LatestScan
    stash: StashSummary
    last_replicate: LatestAdapterRun | None
    last_archive: LatestAdapterRun | None
    audit_chain: AuditChainStatus
    rollups: RollupInfo | None = None


# ─────────────────────── per-section aggregators ──────────────────────────


def _inventory_counts(
    con: sqlite3.Connection,
    *,
    db_path: Path,
    include_imports: bool = False,
) -> InventoryCounts:
    """Five SELECT COUNT(*) plus a UNION-distinct count — cheap.

    The machine count is computed via :func:`count_machines` (opens
    its own read-only connection) so the SQL stays local to the
    machines aggregator. Microseconds at Steward's scale.

    Local table counts stay local (they describe THIS machine's
    pipeline); only the machine count honours ``include_imports``.
    """
    permanodes = int(con.execute("SELECT COUNT(*) FROM permanodes").fetchone()[0])
    current_claims = int(con.execute("SELECT COUNT(*) FROM claims WHERE is_current = 1").fetchone()[0])
    scan_runs = int(con.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0])
    audit_entries = int(con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
    machines = count_machines(db_path=db_path, include_imports=include_imports)
    return InventoryCounts(
        permanodes=permanodes,
        current_claims=current_claims,
        scan_runs=scan_runs,
        audit_entries=audit_entries,
        machines=machines,
    )


def _db_file_info(db_path: Path) -> DbFileInfo:
    """Stat the SQLite file. Best-effort — a missing file falls back to
    zero size + no modified timestamp rather than raising."""
    try:
        st = db_path.stat()
        size = int(st.st_size)
        modified = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
    except OSError:
        size = 0
        modified = None
    return DbFileInfo(path=str(db_path), size_bytes=size, modified_iso=modified)


def _latest_scan(con: sqlite3.Connection) -> LatestScan:
    """Most-recent FINISHED scan_run. Abandoned scans (finished_at IS NULL)
    are skipped — they'd give misleading 'errors=0' readings."""
    row = con.execute(
        """
        SELECT id, root_path, started_at, finished_at,
               files_walked, files_hashed, files_skipped, bytes_hashed, errors
        FROM scan_runs
        WHERE finished_at IS NOT NULL
        ORDER BY started_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return LatestScan(
            scan_run_id=None,
            root_path=None,
            started_at=None,
            finished_at=None,
        )
    return LatestScan(
        scan_run_id=int(row[0]),
        root_path=str(row[1]),
        started_at=str(row[2]),
        finished_at=str(row[3]),
        files_walked=int(row[4] or 0),
        files_hashed=int(row[5] or 0),
        files_skipped=int(row[6] or 0),
        bytes_hashed=int(row[7] or 0),
        errors=int(row[8] or 0),
    )


def _stash_summary(con: sqlite3.Connection) -> StashSummary:
    """Aggregate over in-flight stash entries.

    An entry is "in flight" iff it has a ``stash`` audit row but no
    matching ``stash_finalized`` or ``stash_restored`` row for the same
    claim. We compute this from the audit_log so the summary reflects
    the chain's source-of-truth.
    """
    cur = con.execute(
        """
        WITH stash_calls AS (
            SELECT
                timestamp,
                json_extract(payload_json, '$.manifest_run_id') AS run_id,
                json_extract(payload_json, '$.destination_path') AS dest,
                json_extract(payload_json, '$.source_path') AS src
            FROM audit_log
            WHERE action = 'stash'
        ),
        finalized_or_restored AS (
            SELECT
                json_extract(payload_json, '$.manifest_run_id') AS run_id,
                json_extract(payload_json, '$.destination_path') AS dest
            FROM audit_log
            WHERE action IN ('stash_finalized', 'stash_restored')
        )
        SELECT
            COUNT(*),
            COUNT(DISTINCT s.run_id),
            MIN(s.timestamp),
            MAX(s.timestamp)
        FROM stash_calls s
        WHERE NOT EXISTS (
            SELECT 1 FROM finalized_or_restored f
            WHERE f.run_id IS s.run_id AND f.dest IS s.dest
        )
        """
    ).fetchone()
    n, distinct, oldest, newest = cur
    return StashSummary(
        in_flight_entries=int(n or 0),
        distinct_run_ids=int(distinct or 0),
        oldest_ts_iso=(str(oldest) if oldest is not None else None),
        newest_ts_iso=(str(newest) if newest is not None else None),
    )


def _last_adapter_run(con: sqlite3.Connection, *, end_action: str) -> LatestAdapterRun | None:
    """Fetch the most-recent row with ``action = end_action`` and parse
    its payload. Returns ``None`` when no such row exists yet."""
    row = con.execute(
        "SELECT timestamp, action, payload_json FROM audit_log WHERE action = ? ORDER BY id DESC LIMIT 1",
        (end_action,),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row[2])
    except (TypeError, json.JSONDecodeError):
        payload = {}
    policy = payload.get("policy_name") if isinstance(payload, dict) else None
    return LatestAdapterRun(
        action=str(row[1]),
        timestamp=str(row[0]),
        policy_name=(str(policy) if policy is not None else None),
        payload=payload if isinstance(payload, dict) else {},
    )


def _audit_chain(db_path: Path) -> AuditChainStatus:
    """Wrap :func:`verify_chain` into the status report shape."""
    result = verify_chain(db_path)
    return AuditChainStatus(
        rows_checked=result.rows_checked,
        ok=result.ok,
        error=result.error,
        skipped=False,
    )


_ROLLUP_META_KEY = "status_inventory_rollups"
_DEFAULT_ROLLUP_MAX_AGE = 24 * 3600


def refresh_inventory_rollups(*, db_path: Path, include_imports: bool = False) -> InventoryCounts:
    """Recompute COUNT aggregates and persist them in ``meta``.

    Writes require a read-write connection. Call after large scans /
    imports, or via ``steward status --refresh``.
    """
    from steward.infra.db import repo_meta

    con = connect(db_path, read_only=False, load_vec=False)
    try:
        counts = _inventory_counts(con, db_path=db_path, include_imports=include_imports)
        payload = {
            "permanodes": counts.permanodes,
            "current_claims": counts.current_claims,
            "scan_runs": counts.scan_runs,
            "audit_entries": counts.audit_entries,
            "machines": counts.machines,
            "refreshed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        repo_meta.set_(con, _ROLLUP_META_KEY, json.dumps(payload, sort_keys=True))
        con.commit()
        return counts
    finally:
        con.close()


def _load_inventory_rollups(
    con: sqlite3.Connection,
    *,
    max_age_seconds: int,
) -> tuple[InventoryCounts, str] | None:
    """Return cached counts if present and younger than ``max_age_seconds``."""
    from steward.infra.db import repo_meta

    raw = repo_meta.get(con, _ROLLUP_META_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    refreshed = str(data.get("refreshed_at") or "")
    if not refreshed:
        return None
    try:
        ts = refreshed.replace("Z", "+00:00")
        when = datetime.fromisoformat(ts)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - when).total_seconds()
        if age > max_age_seconds:
            return None
    except (TypeError, ValueError):
        return None
    return (
        InventoryCounts(
            permanodes=int(data["permanodes"]),
            current_claims=int(data["current_claims"]),
            scan_runs=int(data["scan_runs"]),
            audit_entries=int(data["audit_entries"]),
            machines=int(data.get("machines", 0)),
        ),
        refreshed,
    )


def collect_status(
    *,
    db_path: Path,
    include_imports: bool = False,
    quick: bool = False,
    refresh_rollups: bool = False,
    rollup_max_age_seconds: int = _DEFAULT_ROLLUP_MAX_AGE,
) -> StatusReport:
    """Open ``db_path``, run aggregators, return a report.

    Parameters
    ----------
    quick:
        Skip full audit-chain verification and the heavy stash CTE
        (returns empty stash summary). Prefer inventory rollups when
        fresh. Intended for multi‑GB inventories.
    refresh_rollups:
        Force recount of inventory COUNTs and write meta cache.
    rollup_max_age_seconds:
        Max age for accepting cached inventory counts (default 24h).
    """
    db_info = _db_file_info(db_path)
    rollup_info = RollupInfo(used_cache=False, refreshed_at=None)

    if refresh_rollups:
        inventory = refresh_inventory_rollups(db_path=db_path, include_imports=include_imports)
        rollup_info = RollupInfo(
            used_cache=False,
            refreshed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            max_age_seconds=rollup_max_age_seconds,
        )
        con = connect(db_path, read_only=True, load_vec=False)
        try:
            latest_scan = _latest_scan(con)
            stash = StashSummary(0, 0, None, None) if quick else _stash_summary(con)
            last_replicate = _last_adapter_run(con, end_action="replicate_end")
            last_archive = _last_adapter_run(con, end_action="archive_end")
        finally:
            con.close()
    else:
        con = connect(db_path, read_only=True, load_vec=False)
        try:
            cached = _load_inventory_rollups(con, max_age_seconds=rollup_max_age_seconds)
            if cached is not None:
                inventory, refreshed_at = cached
                rollup_info = RollupInfo(
                    used_cache=True,
                    refreshed_at=refreshed_at,
                    max_age_seconds=rollup_max_age_seconds,
                )
            else:
                inventory = _inventory_counts(con, db_path=db_path, include_imports=include_imports)
            latest_scan = _latest_scan(con)
            stash = StashSummary(0, 0, None, None) if quick else _stash_summary(con)
            last_replicate = _last_adapter_run(con, end_action="replicate_end")
            last_archive = _last_adapter_run(con, end_action="archive_end")
        finally:
            con.close()

    if quick:
        audit = AuditChainStatus(rows_checked=0, ok=True, error=None, skipped=True)
    else:
        audit = _audit_chain(db_path)

    return StatusReport(
        db=db_info,
        inventory=inventory,
        latest_scan=latest_scan,
        stash=stash,
        last_replicate=last_replicate,
        last_archive=last_archive,
        audit_chain=audit,
        rollups=rollup_info,
    )


def status_to_dict(report: StatusReport) -> dict[str, Any]:
    """JSON-friendly dict representation of a :class:`StatusReport`."""
    out: dict[str, Any] = {
        "db": asdict(report.db),
        "inventory": asdict(report.inventory),
        "latest_scan": asdict(report.latest_scan),
        "stash": asdict(report.stash),
        "last_replicate": (asdict(report.last_replicate) if report.last_replicate is not None else None),
        "last_archive": (asdict(report.last_archive) if report.last_archive is not None else None),
        "audit_chain": asdict(report.audit_chain),
    }
    if report.rollups is not None:
        out["rollups"] = asdict(report.rollups)
    return out


def _format_bytes(n: int) -> str:
    """Human-readable bytes (binary units). Caps at TiB."""
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:,.1f} {u}" if u != "B" else f"{int(f):,} {u}"
        f /= 1024.0
    return f"{n} B"


__all__ = [
    "AuditChainStatus",
    "DbFileInfo",
    "InventoryCounts",
    "LatestAdapterRun",
    "LatestScan",
    "RollupInfo",
    "StashSummary",
    "StatusReport",
    "_format_bytes",
    "collect_status",
    "refresh_inventory_rollups",
    "status_to_dict",
]
