# SPDX-License-Identifier: Apache-2.0

"""Cold export of audit_log rows for offsite archival / analysis.

Append-only law (ADR-0003): this module **never deletes** audit rows.
Exporting to JSONL is for cold storage and forensic tooling; it does
not shrink ``inventory.db``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from steward.infra.db.connect import connect


@dataclass(frozen=True, slots=True)
class AuditExportResult:
    out_path: Path
    rows_written: int
    first_id: int | None
    last_id: int | None
    before: str | None
    after: str | None


def export_audit_log(
    *,
    db_path: Path,
    out_path: Path,
    before: str | None = None,
    after: str | None = None,
    limit: int | None = None,
    actions: list[str] | None = None,
) -> AuditExportResult:
    """Stream audit_log rows to JSONL.

    Parameters
    ----------
    before, after:
        ISO-8601 timestamps compared to ``audit_log.timestamp`` with
        string order (Steward stores ISO timestamps). Optional.
    limit:
        Max rows (oldest-first within the filtered window).
    actions:
        Optional filter on ``action`` column.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    clauses: list[str] = []
    params: list[Any] = []
    if after:
        clauses.append("timestamp >= ?")
        params.append(after)
    if before:
        clauses.append("timestamp < ?")
        params.append(before)
    if actions:
        placeholders = ",".join("?" for _ in actions)
        clauses.append(f"action IN ({placeholders})")
        params.extend(actions)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    lim = f" LIMIT {int(limit)}" if limit is not None else ""
    sql = (
        "SELECT id, timestamp, machine_id, actor, action, permanode_id, "
        "claim_id, manifest_run_id, payload_json, prev_hash, row_hash "
        f"FROM audit_log{where} ORDER BY id ASC{lim}"
    )

    first_id: int | None = None
    last_id: int | None = None
    n = 0
    con = connect(db_path, read_only=True, load_vec=False)
    try:
        with out_path.open("w", encoding="utf-8") as fh:
            for row in con.execute(sql, params):
                rec = {
                    "id": int(row[0]),
                    "timestamp": row[1],
                    "machine_id": row[2],
                    "actor": row[3],
                    "action": row[4],
                    "permanode_id": row[5],
                    "claim_id": row[6],
                    "manifest_run_id": row[7],
                    "payload_json": row[8],
                    "prev_hash": row[9],
                    "row_hash": row[10],
                    "exported_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                }
                fh.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
                n += 1
                if first_id is None:
                    first_id = int(row[0])
                last_id = int(row[0])
    finally:
        con.close()

    return AuditExportResult(
        out_path=out_path,
        rows_written=n,
        first_id=first_id,
        last_id=last_id,
        before=before,
        after=after,
    )


__all__ = ["AuditExportResult", "export_audit_log"]
