# SPDX-License-Identifier: Apache-2.0

"""Incremental scan — process a specific list of paths into a scan_run.

The full ``scan_root`` walker recurses a tree; ``scan_paths`` takes an
explicit set of files and processes each one (resume-cache aware, same
hash ladder, same claim writes). This is the unit of work the
:class:`steward.core.scanner.watcher.WatcherProtocol` flushes after a
debounced batch.

Like ``scan_root``, this function opens a scan_run row, brackets the work
with scan_start / scan_end audit entries, and returns a
:class:`steward.infra.scanner.walker.ScanStats`. The walker's
``_process_file`` is reused — same code path, same invariants.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from steward.core.hashing import HashLadder
from steward.infra.db import repo_audit
from steward.infra.observability import log_swallowed_error
from steward.infra.scanner.skiplist import is_skipped_file
from steward.infra.scanner.walker import ScanStats, _process_file


def scan_paths(
    *,
    con: sqlite3.Connection,
    paths: Iterable[Path | str],
    machine_id: str,
    ladder: HashLadder | None = None,
    resume_from_run_id: int | None = None,
    include_containers: bool = False,
    notes: str = "steward watch — incremental",
) -> ScanStats:
    """Process a specific list of files into a fresh scan_run.

    Skipped paths (per :mod:`steward.infra.scanner.skiplist`) and paths
    that no longer exist or aren't regular files are counted in
    ``files_skipped`` / ``files_errored`` but don't abort the batch.

    The caller owns the connection. Commit is the caller's responsibility
    (matches ``scan_root``).
    """
    ladder = ladder or HashLadder()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Resolve paths up front, drop duplicates, sort for determinism.
    abs_paths: list[str] = []
    seen: set[str] = set()
    for p in paths:
        resolved = os.path.abspath(str(p))
        if resolved in seen:
            continue
        seen.add(resolved)
        abs_paths.append(resolved)
    abs_paths.sort()

    # Open the scan_run row.
    cur = con.execute(
        """
        INSERT INTO scan_runs (started_at, finished_at, machine_id, root_path,
                               workers, include_containers, resumed_from, notes)
        VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            machine_id,
            "(incremental)",
            1,
            1 if include_containers else 0,
            resume_from_run_id,
            notes,
        ),
    )
    scan_run_id = int(cur.lastrowid or 0)

    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-watch",
        action="scan_start",
        payload={
            "scan_run_id": scan_run_id,
            "incremental": True,
            "path_count": len(abs_paths),
            "resume_from_run_id": resume_from_run_id,
        },
    )

    stats = ScanStats()
    for full in abs_paths:
        if is_skipped_file(os.path.basename(full)):
            stats.files_skipped += 1
            continue
        try:
            st = os.lstat(full)
        except OSError as exc:
            stats.files_errored += 1
            log_swallowed_error("scanner.incremental.lstat", exc, context={"path": full})
            continue
        if not os.path.isfile(full) or os.path.islink(full):
            stats.files_skipped += 1
            continue
        _process_file(
            con,
            full=full,
            st=st,
            scan_run_id=scan_run_id,
            machine_id=machine_id,
            ladder=ladder,
            resume_from_run_id=resume_from_run_id,
            include_containers=include_containers,
            stats=stats,
            now=now,
        )

    ts_end = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute(
        """
        UPDATE scan_runs SET finished_at = ?, files_walked = ?, files_hashed = ?,
                             files_skipped = ?, bytes_hashed = ?, errors = ?
        WHERE id = ?
        """,
        (
            ts_end,
            stats.files_walked,
            stats.files_hashed,
            stats.files_skipped,
            stats.bytes_hashed,
            stats.files_errored,
            scan_run_id,
        ),
    )
    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-watch",
        action="scan_end",
        payload={
            "scan_run_id": scan_run_id,
            "incremental": True,
            "files_walked": stats.files_walked,
            "files_hashed": stats.files_hashed,
            "files_reused": stats.files_reused,
            "files_skipped": stats.files_skipped,
            "files_errored": stats.files_errored,
            "bytes_hashed": stats.bytes_hashed,
            "permanodes_touched": len(stats.permanodes_touched),
        },
    )

    return stats


__all__ = ["scan_paths"]
