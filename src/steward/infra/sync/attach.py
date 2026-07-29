# SPDX-License-Identifier: Apache-2.0

"""Read-only ATTACH helper for cross-machine queries (ADR-0013).

Every read surface that wants to see imported inventories
(``machines list``, ``inspect``, ``stats``, ``dashboard``, MCP read
tools) needs the same pattern: open the local DB, ATTACH each
imported payload via ``?mode=ro`` URI, run UNION ALL queries
across local + attached schemas, DETACH on exit.

This module hides the bookkeeping behind one context manager:

    with attach_imports(db_path=local) as ctx:
        for alias in ctx.aliases:
            ...  # query <alias>.claims, <alias>.permanodes, etc.

The context manager guarantees DETACH on exit even if the body
raises. Errors mid-ATTACH (missing payload file, corrupted .db)
are swallowed via ``log_swallowed_error`` — those inventories are
silently skipped and the operator sees them as MISSING when they
run ``steward db imports list``.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from steward.infra.db.connect import connect
from steward.infra.observability import log_swallowed_error

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger("steward.infra.sync.attach")


@dataclass(frozen=True, slots=True)
class AttachedSchema:
    """One successfully attached imported inventory.

    Aliases are SQL-safe identifiers derived from the imported
    machine_id; callers reference them as ``<alias>.<table>`` in
    UNION ALL queries.
    """

    alias: str
    machine_id: str
    file_path: Path


@dataclass(slots=True)
class AttachContext:
    """Live state of one :func:`attach_imports` session.

    ``aliases`` is the convenience list a UNION ALL builder iterates
    over. ``attached`` keeps the full per-schema metadata for
    callers that want to label results with the originating
    machine_id.
    """

    connection: sqlite3.Connection
    attached: list[AttachedSchema] = field(default_factory=list)

    @property
    def aliases(self) -> list[str]:
        return [s.alias for s in self.attached]

    def by_alias(self) -> dict[str, AttachedSchema]:
        return {s.alias: s for s in self.attached}


def _alias_for(machine_id: str) -> str:
    """Stable SQL identifier for a machine_id (matches
    :mod:`apply_preflight`'s convention)."""
    return "m_" + machine_id.replace("-", "")[:24]


@contextmanager
def attach_imports(
    *,
    db_path: Path,
    read_only_local: bool = True,
) -> "Iterator[AttachContext]":
    """Open ``db_path`` and ATTACH every imported inventory read-only.

    Parameters
    ----------
    db_path:
        Local ``inventory.db``.
    read_only_local:
        When True (the default for read surfaces), the local
        connection is opened RO too. Set False if the caller needs
        to UPDATE a local table (e.g. ``verify_imports`` refreshing
        ``chain_verified_at``).

    Yields an :class:`AttachContext`. On exit, every attached schema
    is detached and the connection closed.

    Attach failures (missing payload, corrupted .db) are logged via
    :func:`log_swallowed_error` and the inventory is skipped; the
    caller's queries simply won't see those rows.
    """
    con = connect(db_path, read_only=read_only_local, load_vec=False)
    ctx = AttachContext(connection=con)
    try:
        rows = con.execute("SELECT machine_id, file_path FROM attached_inventories").fetchall()
        for row in rows:
            machine_id = str(row[0])
            file_path = Path(str(row[1]))
            alias = _alias_for(machine_id)
            try:
                con.execute(f"ATTACH DATABASE 'file:{file_path}?mode=ro' AS {alias}")
            except sqlite3.OperationalError as exc:  # noqa: BLE001
                log_swallowed_error(
                    "infra.sync.attach.attach",
                    exc,
                    context={"path": str(file_path), "alias": alias},
                )
                continue
            ctx.attached.append(
                AttachedSchema(
                    alias=alias,
                    machine_id=machine_id,
                    file_path=file_path,
                )
            )
        yield ctx
    finally:
        for schema in reversed(ctx.attached):
            try:
                con.execute(f"DETACH DATABASE {schema.alias}")
            except sqlite3.OperationalError as exc:  # noqa: BLE001
                log_swallowed_error(
                    "infra.sync.attach.detach",
                    exc,
                    context={"alias": schema.alias},
                )
        con.close()


__all__ = ["AttachContext", "AttachedSchema", "attach_imports"]
