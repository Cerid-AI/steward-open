# SPDX-License-Identifier: Apache-2.0

"""Repository — claims table access."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any


def insert(
    con: sqlite3.Connection,
    *,
    permanode_id: str,
    machine_id: str,
    file_path: str,
    tier: str,
    volume: str,
    size_bytes: int,
    scan_run_id: int,
    domain: str | None = None,
    classification: str | None = None,
    container_path: str | None = None,
    container_sha256: str | None = None,
    mtime_iso: str | None = None,
    observed_at: datetime | None = None,
    legacy_sha256: str | None = None,
) -> int:
    """Insert one claim row and return its id.

    ``parent_dir``, ``basename``, and ``extension`` are derived from
    ``file_path``. Extension is lowercased without the leading dot;
    paths without an extension produce ``None``.
    """
    parent = os.path.dirname(file_path)
    basename = os.path.basename(file_path)
    ext: str | None = None
    if "." in basename:
        ext = basename.rsplit(".", 1)[1].lower() or None
    ts = (observed_at or datetime.now(timezone.utc)).isoformat(timespec="seconds")

    cur = con.execute(
        """
        INSERT INTO claims (
            permanode_id, machine_id, file_path, parent_dir, basename, extension,
            tier, volume, domain, classification,
            container_path, container_sha256, size_bytes, mtime_iso,
            observed_at, scan_run_id, is_current, legacy_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            permanode_id, machine_id, file_path, parent, basename, ext,
            tier, volume, domain, classification,
            container_path, container_sha256, size_bytes, mtime_iso,
            ts, scan_run_id, legacy_sha256,
        ),
    )
    return int(cur.lastrowid or 0)


def count(con: sqlite3.Connection, *, current_only: bool = False) -> int:
    """Return total claim rows; pass ``current_only=True`` to filter to
    ``is_current=1``."""
    if current_only:
        row = con.execute("SELECT COUNT(*) FROM claims WHERE is_current = 1").fetchone()
    else:
        row = con.execute("SELECT COUNT(*) FROM claims").fetchone()
    return int(row[0]) if row else 0


def by_permanode(con: sqlite3.Connection, permanode_id: str) -> list[dict[str, Any]]:
    """Return all claims (current + historical) for one permanode."""
    cur = con.execute(
        """
        SELECT id, machine_id, file_path, tier, volume, domain, classification,
               size_bytes, observed_at, is_current
        FROM claims WHERE permanode_id = ?
        ORDER BY observed_at DESC, id DESC
        """,
        (permanode_id,),
    )
    cols = [d[0] for d in (cur.description or [])]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
