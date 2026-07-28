# SPDX-License-Identifier: Apache-2.0

"""Scanner orchestration facade — opens a DB connection and runs the walker.

Operator-facing helper so the CLI doesn't need to know about
``infra.db.connect`` directly (import-linter contract).
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from steward.infra.db.connect import connect
from steward.infra.scanner.incremental import scan_paths
from steward.infra.scanner.walker import ScanStats, scan_root


def find_latest_finished_run(
    *, db_path: Path, root: Path, machine_id: str
) -> int | None:
    """Look up the most recent FINISHED scan_run for this (root, machine_id).

    "Finished" means ``finished_at IS NOT NULL`` — abandoned (crashed) scans
    are NOT eligible to resume from, since their claim coverage is partial.
    Returns ``None`` when there's no eligible prior run.
    """
    con = connect(db_path, read_only=True, load_vec=False)
    try:
        cur = con.execute(
            """
            SELECT id FROM scan_runs
            WHERE root_path = ? AND machine_id = ? AND finished_at IS NOT NULL
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (str(root), machine_id),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    finally:
        con.close()


def run_scan(
    *,
    root: Path,
    db_path: Path,
    machine_id: str,
    resume: bool = False,
    include_containers: bool = False,
    workers: int = 1,
) -> ScanStats:
    """Open ``db_path``, run :func:`steward.infra.scanner.walker.scan_root`,
    commit, return the stats.

    When ``resume=True``, the orchestrator finds the most recent finished
    scan_run for ``(root, machine_id)`` and passes its id to the walker.
    If no prior finished run exists, this is a no-op fast path — the scan
    runs as if ``resume=False``.

    When ``include_containers=True``, container files (.zip / .tar*)
    encountered during the walk are opened and their members recorded as
    claims with ``container_path`` / ``container_sha256`` populated.

    When ``workers >= 2``, the walker partitions ``root`` by top-level
    subdir and dispatches each subtree to its own worker process. The
    parent walks any loose files at the root level on its own connection
    after workers complete. The audit chain stays linear because only
    the parent writes audit rows.
    """
    resume_from = None
    if resume:
        resume_from = find_latest_finished_run(
            db_path=db_path, root=root, machine_id=machine_id
        )

    con = connect(db_path)
    try:
        stats = scan_root(
            con=con,
            root=root,
            machine_id=machine_id,
            resume_from_run_id=resume_from,
            include_containers=include_containers,
            workers=workers,
            db_path=db_path if workers >= 2 else None,
        )
        con.commit()
    finally:
        con.close()
    return stats


def run_incremental_scan(
    *,
    paths: Iterable[Path | str],
    db_path: Path,
    machine_id: str,
    include_containers: bool = False,
    notes: str = "steward watch — incremental",
) -> ScanStats:
    """Open ``db_path``, run :func:`scan_paths` for ``paths``, commit, return stats.

    The CLI ``steward watch`` calls this once per debounced batch so it
    doesn't need to know about ``infra.db.connect`` directly (import-linter
    contract).
    """
    con = connect(db_path)
    try:
        stats = scan_paths(
            con=con,
            paths=paths,
            machine_id=machine_id,
            include_containers=include_containers,
            notes=notes,
        )
        con.commit()
    finally:
        con.close()
    return stats
