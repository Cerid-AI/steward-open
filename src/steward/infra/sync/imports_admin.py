# SPDX-License-Identifier: Apache-2.0

"""Operator surface for the ``attached_inventories`` table (ADR-0013).

Reads:

* :func:`list_imports` — every row + payload-file-exists check.
* :func:`get_import` — one row, resolved by exact ``machine_id`` or
  by unique prefix (mirrors how ``steward inspect`` accepts UUID
  prefixes).
* :func:`verify_imports` — walk every attached inventory's audit
  chain end-to-end; update ``chain_verified_at`` on success.

Mutations:

* :func:`detach_import` — remove the row, unlink the .db file,
  append a ``inventory_detached`` audit row. Destructive — the CLI
  wrapper requires ``--execute``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from steward.infra.db import repo_audit, repo_meta
from steward.infra.db.connect import connect
from steward.infra.observability import log_swallowed_error

logger = logging.getLogger("steward.infra.sync.imports_admin")


class ImportsAdminError(RuntimeError):
    """Raised when an imports-admin op cannot proceed.

    Covers the standard refusal cases: machine_id not found, prefix
    ambiguous, payload file already missing on detach.
    """


@dataclass(frozen=True, slots=True)
class AttachedInventoryRow:
    """One row from ``attached_inventories`` + a payload-exists flag."""

    machine_id: str
    file_path: Path
    imported_at: str
    exporter_version: str
    exporter_hostname: str | None
    payload_blake3: str
    audit_rows: int
    chain_verified_at: str | None
    notes: str | None
    payload_exists: bool


@dataclass(frozen=True, slots=True)
class DetachResult:
    """Outcome of one :func:`detach_import` call."""

    machine_id: str
    payload_path: Path
    payload_existed: bool
    payload_unlinked: bool
    audit_row_id: int


@dataclass(frozen=True, slots=True)
class ImportVerification:
    """Result of verifying one attached inventory's audit chain."""

    machine_id: str
    payload_path: Path
    payload_exists: bool
    chain_ok: bool
    rows_checked: int
    error: str | None
    verified_at: str | None  # ISO-8601 when the verify ran; None on payload-missing


@dataclass(frozen=True, slots=True)
class VerifyImportsReport:
    """Roll-up across every attached inventory."""

    total: int
    verified: list[ImportVerification]

    @property
    def all_ok(self) -> bool:
        return all(v.chain_ok for v in self.verified)

    @property
    def broken_count(self) -> int:
        return sum(1 for v in self.verified if not v.chain_ok)

    @property
    def missing_count(self) -> int:
        return sum(1 for v in self.verified if not v.payload_exists)


# ─────────────────────── reads ──────────────────────────


def list_imports(*, db_path: Path) -> list[AttachedInventoryRow]:
    """Return every ``attached_inventories`` row, sorted by ``imported_at`` desc."""
    con = connect(db_path, read_only=True, load_vec=False)
    try:
        rows = con.execute(
            """
            SELECT machine_id, file_path, imported_at, exporter_version,
                   exporter_hostname, payload_blake3, audit_rows,
                   chain_verified_at, notes
            FROM attached_inventories
            ORDER BY imported_at DESC, machine_id ASC
            """
        ).fetchall()
    finally:
        con.close()
    return [_row_to_dataclass(r) for r in rows]


def get_import(
    *, db_path: Path, machine_id_or_prefix: str
) -> AttachedInventoryRow:
    """Resolve ``machine_id_or_prefix`` to exactly one attached inventory.

    Accepts the full machine_id or any unique prefix. Raises
    :class:`ImportsAdminError` when there is no match (404) or more
    than one match (ambiguous).
    """
    needle = machine_id_or_prefix.strip()
    if not needle:
        raise ImportsAdminError("machine_id prefix cannot be empty")

    con = connect(db_path, read_only=True, load_vec=False)
    try:
        like_param = needle + "%"
        rows = con.execute(
            """
            SELECT machine_id, file_path, imported_at, exporter_version,
                   exporter_hostname, payload_blake3, audit_rows,
                   chain_verified_at, notes
            FROM attached_inventories
            WHERE machine_id LIKE ?
            ORDER BY machine_id ASC
            """,
            (like_param,),
        ).fetchall()
    finally:
        con.close()

    if not rows:
        raise ImportsAdminError(
            f"no attached inventory matches machine_id prefix {needle!r}"
        )
    if len(rows) > 1:
        matches = ", ".join(str(r[0]) for r in rows[:5])
        raise ImportsAdminError(
            f"prefix {needle!r} matches {len(rows)} attached inventories: "
            f"{matches}{'...' if len(rows) > 5 else ''}"
        )
    return _row_to_dataclass(rows[0])


# ─────────────────────── verify ──────────────────────────


def verify_imports(*, db_path: Path) -> VerifyImportsReport:
    """Walk every attached inventory's audit chain.

    For each row in ``attached_inventories``:

    * If the payload .db is missing, record ``payload_exists=False``
      and ``chain_ok=False`` (the chain can't be verified). No
      mutation on the local DB; the operator should
      ``imports detach`` the stale row.
    * If the payload exists, open it read-only and run
      :func:`steward.infra.db.repo_audit.verify_chain`. On success
      update ``attached_inventories.chain_verified_at`` to the
      current ISO-8601 instant. On failure leave the column
      unchanged so the prior good timestamp (if any) is preserved
      as the last-known-good signal.

    Returns a :class:`VerifyImportsReport` summarizing every row.
    Caller decides what to do with the result (the CLI translates
    into exit code + Rich output).
    """
    rows = list_imports(db_path=db_path)
    if not rows:
        return VerifyImportsReport(total=0, verified=[])

    results: list[ImportVerification] = []
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    chain_ok_machines: list[str] = []

    for row in rows:
        if not row.payload_exists:
            results.append(
                ImportVerification(
                    machine_id=row.machine_id,
                    payload_path=row.file_path,
                    payload_exists=False,
                    chain_ok=False,
                    rows_checked=0,
                    error=f"payload file missing: {row.file_path}",
                    verified_at=None,
                )
            )
            continue
        ok, rows_checked, err = _verify_payload_chain(row.file_path)
        results.append(
            ImportVerification(
                machine_id=row.machine_id,
                payload_path=row.file_path,
                payload_exists=True,
                chain_ok=ok,
                rows_checked=rows_checked,
                error=err,
                verified_at=now_iso if ok else None,
            )
        )
        if ok:
            chain_ok_machines.append(row.machine_id)

    # Persist the new chain_verified_at for the machines we just
    # successfully verified. Single round-trip; the audit chain is
    # NOT touched (this is a read-side attestation, not a mutation
    # event worth chain-logging — and forcing it would create
    # a chain row per verify call which would clutter the log).
    if chain_ok_machines:
        con = connect(db_path, read_only=False, load_vec=False)
        try:
            con.executemany(
                "UPDATE attached_inventories SET chain_verified_at = ? "
                "WHERE machine_id = ?",
                [(now_iso, mid) for mid in chain_ok_machines],
            )
            con.commit()
        finally:
            con.close()

    return VerifyImportsReport(total=len(results), verified=results)


def _verify_payload_chain(payload_path: Path) -> tuple[bool, int, str | None]:
    """Open the payload read-only and run verify_chain.

    Wraps OS-level failures (corrupted file, etc.) so the caller
    gets a structured (False, 0, error) instead of a stack trace.
    """
    try:
        con = connect(payload_path, read_only=True, load_vec=False)
    except Exception as exc:  # noqa: BLE001 — wrap into our refusal shape
        log_swallowed_error(
            "infra.sync.imports_admin.open_payload",
            exc,
            context={"path": str(payload_path)},
        )
        return (False, 0, f"failed to open payload: {exc}")
    try:
        ok, rows, err = repo_audit.verify_chain(con)
    finally:
        con.close()
    return (ok, rows, err)


# ─────────────────────── mutation ──────────────────────────


def detach_import(
    *,
    db_path: Path,
    machine_id_or_prefix: str,
) -> DetachResult:
    """Remove the attached_inventory row, unlink the .db file, audit-log it.

    The local machine's audit chain gets one ``inventory_detached``
    row recording the payload path + blake3 + the original
    imported_at timestamp. If the payload file already doesn't exist
    on disk (operator deleted it manually, or it was on a
    disconnected volume), we proceed anyway — the row removal is
    still meaningful, and the audit row records ``payload_existed:
    False``.

    The CLI requires ``--execute`` (per ADR-0002). The pure function
    has no flag — callers are infrastructure-side and have already
    decided.
    """
    target = get_import(db_path=db_path, machine_id_or_prefix=machine_id_or_prefix)
    local_id = _local_machine_id(db_path)

    payload_existed = target.file_path.exists()
    payload_unlinked = False
    if payload_existed:
        try:
            target.file_path.unlink()
            payload_unlinked = True
        except OSError as exc:  # noqa: BLE001 — fs error shouldn't kill the row removal
            log_swallowed_error(
                "infra.sync.imports_admin.unlink",
                exc,
                context={"path": str(target.file_path)},
            )

    con = connect(db_path, read_only=False, load_vec=False)
    try:
        con.execute(
            "DELETE FROM attached_inventories WHERE machine_id = ?",
            (target.machine_id,),
        )
        audit_id = repo_audit.append(
            con,
            machine_id=local_id,
            actor="steward-db",
            action="inventory_detached",
            payload={
                "detached_machine_id": target.machine_id,
                "payload_path": str(target.file_path),
                "payload_blake3": target.payload_blake3,
                "payload_existed": payload_existed,
                "payload_unlinked": payload_unlinked,
                "originally_imported_at": target.imported_at,
                "exporter_version": target.exporter_version,
                "exporter_hostname": target.exporter_hostname,
                "audit_rows": target.audit_rows,
                "detached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
        con.commit()
    finally:
        con.close()

    # Best-effort: prune the parent dir if it's now empty. Stale
    # <machine_id>/ dirs left behind clutter the imports root and
    # confuse the next operator.
    parent = target.file_path.parent
    try:
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError as exc:  # noqa: BLE001 — directory cleanup is cosmetic
        log_swallowed_error(
            "infra.sync.imports_admin.rmdir",
            exc,
            context={"path": str(parent)},
        )

    return DetachResult(
        machine_id=target.machine_id,
        payload_path=target.file_path,
        payload_existed=payload_existed,
        payload_unlinked=payload_unlinked,
        audit_row_id=audit_id,
    )


# ─────────────────────── helpers ──────────────────────────


def _row_to_dataclass(
    row: tuple[
        str, str, str, str, str | None, str, int, str | None, str | None
    ],
) -> AttachedInventoryRow:
    path = Path(str(row[1]))
    return AttachedInventoryRow(
        machine_id=str(row[0]),
        file_path=path,
        imported_at=str(row[2]),
        exporter_version=str(row[3]),
        exporter_hostname=str(row[4]) if row[4] is not None else None,
        payload_blake3=str(row[5]),
        audit_rows=int(row[6] or 0),
        chain_verified_at=str(row[7]) if row[7] is not None else None,
        notes=str(row[8]) if row[8] is not None else None,
        payload_exists=path.exists(),
    )


def _local_machine_id(db_path: Path) -> str:
    con = connect(db_path, read_only=True, load_vec=False)
    try:
        value = repo_meta.get(con, "machine_id")
    finally:
        con.close()
    if not value:
        raise ImportsAdminError(
            f"local inventory.db has no machine_id in meta: {db_path}"
        )
    return value


__all__ = [
    "AttachedInventoryRow",
    "DetachResult",
    "ImportVerification",
    "ImportsAdminError",
    "VerifyImportsReport",
    "detach_import",
    "get_import",
    "list_imports",
    "verify_imports",
]
