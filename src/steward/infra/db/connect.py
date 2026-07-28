# SPDX-License-Identifier: Apache-2.0

"""SQLite connection helper — WAL, sqlite-vec, foreign-key enforcement.

Steward stores the entire inventory + audit + embeddings in a single
SQLite file (ADR-0006). Every connection that opens it goes through
:func:`connect` so the pragmas + extension load are consistent.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from steward.infra.observability import log_swallowed_error

logger = logging.getLogger("steward.infra.db")

DEFAULT_PAGE_CACHE_KIB = 65536  # 64 MiB negative-form cache_size pragma


def connect(
    path: str | Path,
    *,
    read_only: bool = False,
    load_vec: bool = True,
    detect_types: int = sqlite3.PARSE_DECLTYPES,
) -> sqlite3.Connection:
    """Open ``path`` with Steward's canonical pragmas.

    Pragmas applied:

    * ``journal_mode=WAL`` — concurrent readers + a single writer; the audit
      trigger semantics depend on WAL's serialised-writer model.
    * ``synchronous=NORMAL`` — durable across power-loss for committed
      transactions in WAL mode.
    * ``foreign_keys=ON`` — claims → permanodes is enforced.
    * ``cache_size=-65536`` — 64 MiB per connection.

    The ``sqlite-vec`` extension is loaded by default. The caller may
    pass ``load_vec=False`` for connections that never query the vector
    table (e.g. the audit-chain verify path), avoiding the extension's
    static-init overhead.
    """
    target = Path(path)
    if not read_only:
        target.parent.mkdir(parents=True, exist_ok=True)
    uri = f"file:{target}{'?mode=ro' if read_only else ''}"
    con = sqlite3.connect(uri, uri=True, detect_types=detect_types)

    # Pragmas — order matters: WAL must be set before any writes for the
    # file's journal-mode header to be persisted.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute(f"PRAGMA cache_size=-{DEFAULT_PAGE_CACHE_KIB}")

    if load_vec:
        try:
            import sqlite_vec

            con.enable_load_extension(True)
            sqlite_vec.load(con)
            con.enable_load_extension(False)
        except Exception as exc:  # noqa: BLE001 — extension load is optional
            log_swallowed_error(
                "infra.db.connect.sqlite_vec",
                exc,
                context={"path": str(target)},
            )

    return con


def vec_version(con: sqlite3.Connection) -> str | None:
    """Return ``vec_version()`` from sqlite-vec, or ``None`` when the
    extension isn't loaded. Useful for ``steward db migrate`` to log the
    available extension version at migrate time.
    """
    try:
        row = con.execute("SELECT vec_version()").fetchone()
        return str(row[0]) if row else None
    except sqlite3.OperationalError:
        return None
