# SPDX-License-Identifier: Apache-2.0

"""SQLite online-backup snapshots of ``inventory.db``.

A one-shot snapshot path that uses SQLite's ``Connection.backup``
API. Unlike a raw ``cp`` of the WAL'd file (which races writes and
loses the WAL contents), the online-backup API produces a fully
consistent copy without blocking writers.

The snapshot is written *to a file* (not a directory). The caller
picks the path; we never silently choose. Each snapshot is named so
the operator can reproduce it: typical pattern is
``inventory-<iso8601>.db``.

Each call appends a ``db_backup_created`` audit entry to the SOURCE
database so the chain attests to "we made a copy at time T into path
P with size N." The snapshot itself is a self-contained DB; its own
audit chain is a prefix of the source's chain up to the snapshot
moment.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from steward.infra.db import repo_audit
from steward.infra.db.connect import connect


@dataclass(frozen=True, slots=True)
class BackupResult:
    """Outcome of one ``backup_inventory_db`` call."""

    source_path: Path
    target_path: Path
    bytes_copied: int
    duration_seconds: float


class BackupError(RuntimeError):
    """Raised when the snapshot can't be created.

    Covers the two common cases: target's parent directory doesn't
    exist (we don't auto-mkdir; the operator decides where snapshots
    live), and target already exists (we never overwrite without
    explicit opt-in via ``overwrite=True``).
    """


def backup_inventory_db(
    *,
    source_path: Path,
    target_path: Path,
    machine_id: str,
    overwrite: bool = False,
    pages_per_step: int = 1024,
) -> BackupResult:
    """Copy ``source_path`` to ``target_path`` via SQLite's online-backup.

    Parameters
    ----------
    source_path:
        Existing inventory.db.
    target_path:
        Destination file. Refuses to overwrite an existing file unless
        ``overwrite=True``.
    machine_id:
        Used for the ``db_backup_created`` audit entry on the source.
    overwrite:
        When True, an existing ``target_path`` is unlinked first.
        Default False — operators almost always want to write to a
        fresh path so they can tell snapshots apart.
    pages_per_step:
        SQLite's backup-step batch size in pages. Higher values block
        the writer for longer per step but copy faster. 1024 (= 4 MiB
        with the default page_size) is a sane middle ground.

    Returns a :class:`BackupResult` with the byte count + wall-clock
    duration. Raises :class:`BackupError` on refusal-to-overwrite or
    when the source doesn't exist.
    """
    if not source_path.exists():
        raise BackupError(f"source inventory.db not found: {source_path}")
    if target_path.exists() and not overwrite:
        raise BackupError(
            f"target already exists: {target_path}. "
            f"Pass overwrite=True to replace it."
        )
    if not target_path.parent.exists():
        raise BackupError(
            f"target's parent directory does not exist: {target_path.parent}"
        )

    started = time.monotonic()

    # Open source via the project helper so PRAGMAs (WAL, foreign keys,
    # sqlite-vec) match the rest of the codebase. The destination is
    # opened raw — we don't want the new file to load sqlite-vec at
    # backup time, and the schema is copied page-by-page regardless.
    src = connect(source_path, read_only=False, load_vec=False)
    if target_path.exists() and overwrite:
        target_path.unlink()
    dst = sqlite3.connect(str(target_path))
    try:
        src.backup(dst, pages=int(pages_per_step))
    finally:
        dst.close()
        # Don't close src yet — we still need to append the audit row
        # AFTER the backup so the snapshot itself doesn't contain its
        # own "creation" row.

    duration = time.monotonic() - started
    size = target_path.stat().st_size

    # Append audit entry on the source DB. The snapshot is already
    # written and closed; this row only exists in the live database.
    try:
        repo_audit.append(
            src,
            machine_id=machine_id,
            actor="steward-db",
            action="db_backup_created",
            payload={
                "source": str(source_path),
                "target": str(target_path),
                "bytes": size,
                "duration_seconds": round(duration, 3),
            },
        )
        src.commit()
    finally:
        src.close()

    return BackupResult(
        source_path=source_path,
        target_path=target_path,
        bytes_copied=int(size),
        duration_seconds=duration,
    )


__all__ = ["BackupError", "BackupResult", "backup_inventory_db"]
