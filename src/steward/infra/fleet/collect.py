# SPDX-License-Identifier: Apache-2.0

"""Compose multi-machine fleet health matrix (ADR-0021).

Read-only: ATTACH RO for imports, no claim mutation, no full attached
chain verify on the quick path.
"""

from __future__ import annotations

import socket
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from steward.core.fleet.evaluate import (
    age_hours_from_iso,
    build_envelope_sla,
    build_fleet_checks,
    chain_level_for_attached,
    chain_level_for_local,
    compute_fleet_overall,
    envelope_level_for_attached,
    envelope_level_for_local,
    row_rollup_level,
    scan_level_for_row,
)
from steward.core.fleet.types import (
    DEFAULT_FLEET_THRESHOLDS,
    FleetHealthMatrix,
    FleetThresholds,
    MachineHealthRow,
)
from steward.core.health.model import HealthLevel
from steward.infra.db.admin import resolve_machine_id
from steward.infra.db.connect import connect
from steward.infra.observability import log_swallowed_error


def collect_fleet_health(
    *,
    db_path: Path,
    include_imports: bool = True,
    quick: bool = True,
    thresholds: FleetThresholds | None = None,
    data_dir: Path | None = None,
    now: datetime | None = None,
) -> FleetHealthMatrix:
    """Build a :class:`FleetHealthMatrix` for local (+ attached) machines.

    Parameters
    ----------
    include_imports:
        Default True for this command (unlike ``machines list``).
    quick:
        Skip full local audit-chain verify; attached uses
        ``chain_verified_at`` + ``payload_exists`` only.
    data_dir:
        Optional Steward data dir for ``exports/`` mtime fallback when no
        ``db_export_created`` audit row exists.
    """
    thr = thresholds or DEFAULT_FLEET_THRESHOLDS
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    generated_at = ref.isoformat(timespec="seconds")
    target = Path(db_path).expanduser()

    local_machine_id = "unknown"
    try:
        local_machine_id = resolve_machine_id(target)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "fleet.collect.machine_id",
            exc,
            context={"db_path": str(target)},
        )

    hostname = _local_hostname()
    rows: list[MachineHealthRow] = []

    # Local row
    local_row = _collect_local_row(
        target,
        machine_id=local_machine_id,
        hostname=hostname,
        thr=thr,
        now=ref,
        quick=quick,
        data_dir=data_dir,
    )
    if local_row is not None:
        rows.append(local_row)

    # Attached rows
    if include_imports:
        rows.extend(
            _collect_attached_rows(
                target,
                thr=thr,
                now=ref,
            )
        )

    # Ensure local is first
    rows.sort(key=lambda r: (0 if r.source == "local" else 1, r.machine_id))

    envelope_sla = build_envelope_sla(rows)
    checks = build_fleet_checks(rows, envelope_sla, thresholds=thr)
    overall = compute_fleet_overall(rows, checks)

    notes: list[str] = []
    if quick:
        notes.append(
            "quick=true: local chain unknown; attached uses chain_verified_at only"
        )
    if not include_imports:
        notes.append("include_imports=false: local-only matrix")
    if envelope_sla.local_export_at is None and any(r.source == "local" for r in rows):
        notes.append("no db_export_created audit row (and no exports/ mtime fallback)")

    return FleetHealthMatrix(
        generated_at=generated_at,
        local_machine_id=local_machine_id,
        overall=overall,
        thresholds=thr,
        rows=tuple(rows),
        envelope_sla=envelope_sla,
        checks=tuple(checks),
        notes=tuple(notes),
        quick=quick,
        include_imports=include_imports,
    )


def fleet_health_to_dict(matrix: FleetHealthMatrix) -> dict[str, Any]:
    """JSON-stable serialization for CLI --json, MCP, dashboard."""
    return {
        "generated_at": matrix.generated_at,
        "local_machine_id": matrix.local_machine_id,
        "overall": matrix.overall,
        "quick": matrix.quick,
        "include_imports": matrix.include_imports,
        "thresholds": asdict(matrix.thresholds),
        "rows": [asdict(r) for r in matrix.rows],
        "envelope_sla": asdict(matrix.envelope_sla),
        "checks": [asdict(c) for c in matrix.checks],
        "notes": list(matrix.notes),
    }


def fleet_health_to_compact_dict(matrix: FleetHealthMatrix) -> dict[str, Any]:
    """Compact form for estate snapshot series (no full claim tables)."""
    from steward.core.fleet.evaluate import fleet_section_from_matrix

    section = fleet_section_from_matrix(matrix)
    section["generated_at"] = matrix.generated_at
    section["local_machine_id"] = matrix.local_machine_id
    section["quick"] = matrix.quick
    return section


# ─────────────────────── local row ──────────────────────────


def _collect_local_row(
    db_path: Path,
    *,
    machine_id: str,
    hostname: str | None,
    thr: FleetThresholds,
    now: datetime,
    quick: bool,
    data_dir: Path | None,
) -> MachineHealthRow | None:
    try:
        con = connect(db_path, read_only=True, load_vec=False)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "fleet.collect.local_open",
            exc,
            context={"db_path": str(db_path)},
        )
        return None
    try:
        claim_count, current_claim_count, audit_entry_count = _claim_audit_counts(
            con, machine_id=machine_id, schema_prefix=""
        )
        scan_finished, scan_root, scan_errors = _latest_finished_scan(
            con, machine_id=machine_id, schema_prefix=""
        )
        scan_age = age_hours_from_iso(scan_finished, now=now)
        scan_lvl = scan_level_for_row(
            scan_age, thresholds=thr, has_finished=scan_finished is not None
        )

        audit_ok: bool | None = None
        audit_skipped = quick
        if not quick:
            try:
                from steward.infra.db import repo_audit

                ok, _n, _err = repo_audit.verify_chain(con)
                audit_ok = bool(ok)
                audit_skipped = False
            except Exception as exc:  # noqa: BLE001
                log_swallowed_error(
                    "fleet.collect.local_verify",
                    exc,
                    context={"db_path": str(db_path)},
                )
                audit_ok = False
                audit_skipped = False

        chain_lvl = chain_level_for_local(
            quick=quick, audit_ok=audit_ok, audit_skipped=audit_skipped
        )
        chain_verified_at: str | None = None
        chain_age: float | None = None
        if audit_ok is True:
            chain_verified_at = now.isoformat(timespec="seconds")
            chain_age = 0.0

        envelope_at = _latest_export_audit_ts(con)
        if envelope_at is None and data_dir is not None:
            envelope_at = _exports_dir_mtime_iso(data_dir)
        envelope_age = age_hours_from_iso(envelope_at, now=now)
        envelope_lvl = envelope_level_for_local(envelope_age, thresholds=thr)

        row = MachineHealthRow(
            machine_id=machine_id,
            hostname=hostname,
            source="local",
            is_current=True,
            claim_count=claim_count,
            current_claim_count=current_claim_count,
            last_scan_finished_at=scan_finished,
            last_scan_root=scan_root,
            last_scan_errors=scan_errors,
            scan_age_hours=scan_age,
            scan_level=scan_lvl,
            chain_verified_at=chain_verified_at,
            chain_age_hours=chain_age,
            chain_level=chain_lvl,
            payload_exists=None,
            envelope_at=envelope_at,
            envelope_age_hours=envelope_age,
            envelope_level=envelope_lvl,
            audit_entry_count=audit_entry_count,
            schema_version=None,
            payload_blake3=None,
            level="unknown",
        )
        return MachineHealthRow(
            **{**asdict(row), "level": row_rollup_level(row)}
        )
    finally:
        con.close()


# ─────────────────────── attached rows ──────────────────────────


def _collect_attached_rows(
    db_path: Path,
    *,
    thr: FleetThresholds,
    now: datetime,
) -> list[MachineHealthRow]:
    out: list[MachineHealthRow] = []
    try:
        from steward.infra.sync.imports_admin import list_imports

        imports = list_imports(db_path=db_path)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "fleet.collect.list_imports",
            exc,
            context={"db_path": str(db_path)},
        )
        return out

    if not imports:
        return out

    # Prefer ATTACH for claim/scan counts when payload exists.
    alias_by_mid: dict[str, str] = {}
    try:
        from steward.infra.sync.attach import attach_imports

        with attach_imports(db_path=db_path) as ctx:
            for schema in ctx.attached:
                alias_by_mid[schema.machine_id] = schema.alias
            for row in imports:
                mid = row.machine_id
                alias = alias_by_mid.get(mid)
                claim_count = 0
                current_claim_count = 0
                audit_entry_count = row.audit_rows
                scan_finished: str | None = None
                scan_root: str | None = None
                scan_errors: int | None = None
                if alias is not None and row.payload_exists:
                    try:
                        claim_count, current_claim_count, ae = _claim_audit_counts(
                            ctx.connection,
                            machine_id=mid,
                            schema_prefix=f"{alias}.",
                        )
                        if ae:
                            audit_entry_count = ae
                        scan_finished, scan_root, scan_errors = _latest_finished_scan(
                            ctx.connection,
                            machine_id=mid,
                            schema_prefix=f"{alias}.",
                        )
                    except Exception as exc:  # noqa: BLE001
                        log_swallowed_error(
                            "fleet.collect.attached_query",
                            exc,
                            context={"machine_id": mid, "alias": alias},
                        )

                scan_age = age_hours_from_iso(scan_finished, now=now)
                scan_lvl = scan_level_for_row(
                    scan_age,
                    thresholds=thr,
                    has_finished=scan_finished is not None,
                )
                chain_age = age_hours_from_iso(row.chain_verified_at, now=now)
                chain_lvl = chain_level_for_attached(
                    payload_exists=row.payload_exists,
                    chain_verified_at=row.chain_verified_at,
                    chain_age_hours=chain_age,
                    thresholds=thr,
                )
                envelope_age = age_hours_from_iso(row.imported_at, now=now)
                envelope_lvl = envelope_level_for_attached(
                    envelope_age,
                    thresholds=thr,
                    payload_exists=row.payload_exists,
                )
                mrow = MachineHealthRow(
                    machine_id=mid,
                    hostname=row.exporter_hostname,
                    source="attached",
                    is_current=False,
                    claim_count=claim_count,
                    current_claim_count=current_claim_count,
                    last_scan_finished_at=scan_finished,
                    last_scan_root=scan_root,
                    last_scan_errors=scan_errors,
                    scan_age_hours=scan_age,
                    scan_level=scan_lvl,
                    chain_verified_at=row.chain_verified_at,
                    chain_age_hours=chain_age,
                    chain_level=chain_lvl,
                    payload_exists=row.payload_exists,
                    envelope_at=row.imported_at,
                    envelope_age_hours=envelope_age,
                    envelope_level=envelope_lvl,
                    audit_entry_count=audit_entry_count,
                    schema_version=row.exporter_version,
                    payload_blake3=row.payload_blake3,
                    level="unknown",
                )
                out.append(
                    MachineHealthRow(
                        **{
                            **asdict(mrow),
                            "level": row_rollup_level(mrow),
                        }
                    )
                )
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "fleet.collect.attach",
            exc,
            context={"db_path": str(db_path)},
        )
        # Fallback: build rows from imports registry only (no claim counts).
        if not out:
            for row in imports:
                envelope_age = age_hours_from_iso(row.imported_at, now=now)
                chain_age = age_hours_from_iso(row.chain_verified_at, now=now)
                chain_lvl = chain_level_for_attached(
                    payload_exists=row.payload_exists,
                    chain_verified_at=row.chain_verified_at,
                    chain_age_hours=chain_age,
                    thresholds=thr,
                )
                envelope_lvl = envelope_level_for_attached(
                    envelope_age,
                    thresholds=thr,
                    payload_exists=row.payload_exists,
                )
                mrow = MachineHealthRow(
                    machine_id=row.machine_id,
                    hostname=row.exporter_hostname,
                    source="attached",
                    is_current=False,
                    claim_count=0,
                    current_claim_count=0,
                    scan_level="fail",
                    chain_verified_at=row.chain_verified_at,
                    chain_age_hours=chain_age,
                    chain_level=chain_lvl,
                    payload_exists=row.payload_exists,
                    envelope_at=row.imported_at,
                    envelope_age_hours=envelope_age,
                    envelope_level=envelope_lvl,
                    audit_entry_count=row.audit_rows,
                    schema_version=row.exporter_version,
                    payload_blake3=row.payload_blake3,
                    level="unknown",
                )
                out.append(
                    MachineHealthRow(
                        **{
                            **asdict(mrow),
                            "level": row_rollup_level(mrow),
                        }
                    )
                )
    return out


# ─────────────────────── SQL helpers ──────────────────────────


def _claim_audit_counts(
    con: sqlite3.Connection,
    *,
    machine_id: str,
    schema_prefix: str,
) -> tuple[int, int, int]:
    """Return (claim_count, current_claim_count, audit_entry_count)."""
    # nosec B608 — schema_prefix from controlled allowlist ("" or m_*)
    claims_sql = (
        f"SELECT COUNT(*), "
        f"SUM(CASE WHEN is_current = 1 THEN 1 ELSE 0 END) "
        f"FROM {schema_prefix}claims WHERE machine_id = ?"  # nosec B608
    )
    audit_sql = (
        f"SELECT COUNT(*) FROM {schema_prefix}audit_log WHERE machine_id = ?"  # nosec B608
    )
    crow = con.execute(claims_sql, (machine_id,)).fetchone()
    arow = con.execute(audit_sql, (machine_id,)).fetchone()
    total = int(crow[0] or 0) if crow else 0
    current = int(crow[1] or 0) if crow else 0
    audits = int(arow[0] or 0) if arow else 0
    return total, current, audits


def _latest_finished_scan(
    con: sqlite3.Connection,
    *,
    machine_id: str,
    schema_prefix: str,
) -> tuple[str | None, str | None, int | None]:
    """Latest finished scan_run for machine_id → (finished_at, root, errors)."""
    sql = (
        f"SELECT finished_at, root_path, errors FROM {schema_prefix}scan_runs "  # nosec B608
        f"WHERE machine_id = ? AND finished_at IS NOT NULL "
        f"ORDER BY finished_at DESC LIMIT 1"
    )
    row = con.execute(sql, (machine_id,)).fetchone()
    if row is None:
        return None, None, None
    return (
        str(row[0]) if row[0] is not None else None,
        str(row[1]) if row[1] is not None else None,
        int(row[2] or 0) if row[2] is not None else 0,
    )


def _latest_export_audit_ts(con: sqlite3.Connection) -> str | None:
    row = con.execute(
        """
        SELECT timestamp FROM audit_log
        WHERE action = 'db_export_created'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return str(row[0])


def _exports_dir_mtime_iso(data_dir: Path) -> str | None:
    """Best-effort newest file mtime under ``data_dir/exports/``."""
    exports = Path(data_dir) / "exports"
    try:
        if not exports.is_dir():
            return None
        newest: float | None = None
        for p in exports.iterdir():
            if not p.is_file():
                continue
            try:
                m = p.stat().st_mtime
            except OSError as exc:
                log_swallowed_error(
                    "fleet.collect.exports_mtime",
                    exc,
                    context={"path": str(p)},
                )
                continue
            if newest is None or m > newest:
                newest = m
        if newest is None:
            return None
        return datetime.fromtimestamp(newest, tz=timezone.utc).isoformat(timespec="seconds")
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "fleet.collect.exports_dir",
            exc,
            context={"data_dir": str(data_dir)},
        )
        return None


def _local_hostname() -> str | None:
    try:
        return socket.gethostname() or None
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("fleet.collect.hostname", exc, context={})
        return None


# Silence unused import warning for HealthLevel re-export use in typing notes
_ = HealthLevel

__all__ = [
    "collect_fleet_health",
    "fleet_health_to_compact_dict",
    "fleet_health_to_dict",
]
