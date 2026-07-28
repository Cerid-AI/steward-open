# SPDX-License-Identifier: Apache-2.0

"""Repository — permanodes table access."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from steward.core.ids import permanode_id


def upsert(
    con: sqlite3.Connection,
    canonical_hash: str,
    size_bytes: int,
    *,
    algo: str = "blake3",
    observed_at: datetime | None = None,
) -> str:
    """Insert-or-touch a permanode and return its id.

    Idempotent: a second call with the same ``(canonical_hash, size_bytes)``
    only updates ``last_seen_at``. The deterministic id is returned either
    way — callers don't need to know if the row was new.
    """
    ts = (observed_at or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    pid = permanode_id(canonical_hash, size_bytes)
    con.execute(
        """
        INSERT INTO permanodes (id, canonical_hash, canonical_hash_algo, size_bytes,
                                first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_hash, size_bytes) DO UPDATE SET last_seen_at=excluded.last_seen_at
        """,
        (pid, canonical_hash.strip().lower(), algo, size_bytes, ts, ts),
    )
    return pid


def get_by_hash(con: sqlite3.Connection, canonical_hash: str, size_bytes: int) -> str | None:
    """Return the permanode id for ``(hash, size)``, or ``None`` if absent."""
    row = con.execute(
        "SELECT id FROM permanodes WHERE canonical_hash = ? AND size_bytes = ?",
        (canonical_hash.strip().lower(), size_bytes),
    ).fetchone()
    return None if row is None else str(row[0])


def count(con: sqlite3.Connection) -> int:
    """Return total permanode rows."""
    row = con.execute("SELECT COUNT(*) FROM permanodes").fetchone()
    return int(row[0]) if row else 0
