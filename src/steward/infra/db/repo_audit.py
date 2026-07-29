# SPDX-License-Identifier: Apache-2.0

"""Repository — audit_log append-only writer + chain verification.

The audit_log table is the tamper-evidence layer (ADR-0003). Every row
hash-chains to its predecessor; mutating any historical row fails the
verify pass. SQLite triggers in 0001_initial.py raise ABORT on UPDATE
or DELETE, so even an operator with the ``sqlite3`` CLI can't tamper
silently — and any successful tamper still breaks the chain.

Use :func:`append` to write rows; use :func:`verify_chain` from
``steward db verify`` to walk the table and confirm integrity.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from steward.core.audit import GENESIS_PREV_HASH, compute_row_hash
from steward.core.errors import AuditChainBroken


def _last_row_hash(con: sqlite3.Connection) -> str:
    """Return the most-recent row's ``row_hash``, or genesis if the table is empty."""
    row = con.execute("SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    return str(row[0]) if row else GENESIS_PREV_HASH


def append(
    con: sqlite3.Connection,
    *,
    machine_id: str,
    actor: str,
    action: str,
    payload: dict[str, Any],
    permanode_id: str | None = None,
    claim_id: int | None = None,
    manifest_run_id: str | None = None,
    timestamp: datetime | None = None,
) -> int:
    """Append one audit row inside the caller's transaction.

    The caller is responsible for ``con.commit()``. This is intentional:
    audit rows MUST land in the same transaction as the data write they
    describe, so a crash between the data write and the audit append
    can't leave them inconsistent.

    Returns the new row's id.
    """
    ts = (timestamp or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    prev = _last_row_hash(con)

    # Build the canonical payload dict from the columns we'll store, then hash.
    canonical = {
        "timestamp": ts,
        "machine_id": machine_id,
        "actor": actor,
        "action": action,
        "permanode_id": permanode_id,
        "claim_id": claim_id,
        "manifest_run_id": manifest_run_id,
        "payload_json": payload_json,
    }
    row_hash = compute_row_hash(prev, canonical)

    cur = con.execute(
        """
        INSERT INTO audit_log (timestamp, machine_id, actor, action, permanode_id,
                               claim_id, manifest_run_id, payload_json, prev_hash, row_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ts, machine_id, actor, action, permanode_id, claim_id, manifest_run_id, payload_json, prev, row_hash),
    )
    return int(cur.lastrowid or 0)


def count(con: sqlite3.Connection) -> int:
    """Return total audit rows."""
    row = con.execute("SELECT COUNT(*) FROM audit_log").fetchone()
    return int(row[0]) if row else 0


def verify_chain(con: sqlite3.Connection) -> tuple[bool, int, str | None]:
    """Walk the audit_log in id order, recomputing each row_hash.

    Returns ``(ok, rows_checked, error_message_or_None)``. An empty table
    returns ``(True, 0, None)`` — the chain is trivially intact.

    Raises :class:`AuditChainBroken` is NOT used here; the boolean is the
    machine-readable signal and the CLI surfaces a friendly message. The
    exception class exists for non-CLI callers that prefer raising.
    """
    cur = con.execute(
        """
        SELECT id, timestamp, machine_id, actor, action, permanode_id,
               claim_id, manifest_run_id, payload_json, prev_hash, row_hash
        FROM audit_log ORDER BY id ASC
        """
    )
    prev_expected = GENESIS_PREV_HASH
    rows_checked = 0
    for row in cur:
        rows_checked += 1
        (
            rid,
            ts,
            machine_id,
            actor,
            action,
            permanode_id,
            claim_id,
            manifest_run_id,
            payload_json,
            prev_hash,
            row_hash,
        ) = row
        if prev_hash != prev_expected:
            return (False, rows_checked, f"row {rid}: prev_hash mismatch (expected {prev_expected}, got {prev_hash})")
        canonical = {
            "timestamp": ts,
            "machine_id": machine_id,
            "actor": actor,
            "action": action,
            "permanode_id": permanode_id,
            "claim_id": claim_id,
            "manifest_run_id": manifest_run_id,
            "payload_json": payload_json,
        }
        recomputed = compute_row_hash(prev_hash, canonical)
        if recomputed != row_hash:
            return (False, rows_checked, f"row {rid}: row_hash mismatch (recomputed {recomputed}, stored {row_hash})")
        prev_expected = row_hash
    return (True, rows_checked, None)


def raise_if_broken(con: sqlite3.Connection) -> None:
    """Convenience: run :func:`verify_chain` and raise if broken."""
    ok, _n, err = verify_chain(con)
    if not ok:
        raise AuditChainBroken(err or "audit chain broken")


def by_manifest_run(con: sqlite3.Connection, manifest_run_id: str) -> Iterable[dict[str, Any]]:
    """Yield every audit row produced by one ``steward apply`` invocation."""
    cur = con.execute(
        """
        SELECT id, timestamp, action, permanode_id, claim_id, payload_json
        FROM audit_log WHERE manifest_run_id = ? ORDER BY id ASC
        """,
        (manifest_run_id,),
    )
    cols = [d[0] for d in (cur.description or [])]
    for r in cur.fetchall():
        yield dict(zip(cols, r, strict=True))
