# SPDX-License-Identifier: Apache-2.0

"""Meta-table accessor — key/value store for schema-version, machine-id, etc."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def get(con: sqlite3.Connection, key: str) -> str | None:
    """Return the meta value for ``key``, or ``None`` if absent."""
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def set_(con: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert ``key=value`` with ``updated_at = now()``.

    The trailing underscore avoids shadowing the built-in :func:`set`.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute(
        """
        INSERT INTO meta (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value, now),
    )


def all_(con: sqlite3.Connection) -> dict[str, str]:
    """Return the entire meta table as a dict."""
    return {str(k): str(v) for k, v in con.execute("SELECT key, value FROM meta")}
