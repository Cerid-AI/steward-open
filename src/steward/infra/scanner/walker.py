# SPDX-License-Identifier: Apache-2.0

"""Filesystem walker — produces claims into Steward's inventory.

For each regular file under ``root``:

1. Compute the xxh3-128 fast hash.
2. If the size is large or the xxh3 hits a known permanode, also compute
   blake3 (the ladder; see :class:`steward.core.hashing.HashLadder`).
3. Upsert the permanode keyed on ``(canonical_hash, size_bytes)`` —
   canonical_hash is blake3 when promoted, else xxh3.
4. Insert a claim row tying the path + tier + scan_run to that permanode.

The walker has two modes:

* **Single-process** (``workers=1``, default) — one connection, one walk,
  no IPC. Best for small trees or interactive scans.
* **Subtree-disjoint parallel** (``workers>=2``) — the parent commits the
  scan_run row + scan_start audit, then a ``ProcessPoolExecutor`` walks
  each top-level subdir in its own process and its own connection. The
  parent walks any loose files at the root level after workers finish,
  then writes the scan_end audit. The audit chain stays linear because
  only the parent writes audit rows; permanode upserts are atomic
  (``ON CONFLICT``) so worker races against the same content are safe.

Skipped paths (per :mod:`steward.infra.scanner.skiplist`) are counted in
``scan_runs.files_skipped`` but otherwise leave no trace.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from steward.core.hashing import HashLadder
from steward.core.tiers import classify_tier
from steward.infra.db import repo_audit, repo_claims, repo_permanodes
from steward.infra.observability import log_swallowed_error
from steward.infra.scanner.container_walker import (
    is_container_path,
    walk_container,
)
from steward.infra.scanner.skiplist import (
    filter_dirs,
    filter_files,
    is_skipped_dir,
    is_skipped_file,
)

logger = logging.getLogger("steward.infra.scanner.walker")


@dataclass
class ScanStats:
    files_walked: int = 0
    files_hashed: int = 0
    files_reused: int = 0  # resume hits — reused permanode_id from prior scan_run
    files_skipped: int = 0
    files_errored: int = 0
    bytes_hashed: int = 0
    containers_walked: int = 0
    containers_skipped: int = 0  # unsupported (.dmg / .7z / .rar pending v0.2)
    containers_errored: int = 0
    container_members_walked: int = 0
    container_members_errored: int = 0
    permanodes_touched: set[str] = field(default_factory=set)


def _walk_files(root: Path) -> Iterator[tuple[str, os.stat_result]]:
    """Yield ``(absolute_path, stat_result)`` for every non-skipped file
    under ``root``. Directories listed in
    :data:`steward.infra.scanner.skiplist.DEFAULT_SKIP_DIRS` are pruned
    in place; symlinks are not followed.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        # In-place filter so os.walk doesn't recurse into noise dirs.
        dirnames[:] = filter_dirs(dirnames)
        for fname in filter_files(filenames):
            full = os.path.join(dirpath, fname)
            try:
                st = os.lstat(full)
            except OSError as exc:
                log_swallowed_error("scanner.walker.lstat", exc, context={"path": full})
                continue
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            yield full, st


def _resume_cache_lookup(
    con: sqlite3.Connection,
    *,
    resume_from_run_id: int,
    machine_id: str,
    file_path: str,
) -> tuple[str, int, str] | None:
    """Return ``(permanode_id, size_bytes, mtime_iso)`` from the prior
    scan_run's claim for this path, or ``None`` if there's no record.

    The caller decides whether to reuse based on whether the current
    ``stat`` matches the cached size + mtime.
    """
    cur = con.execute(
        """
        SELECT permanode_id, size_bytes, mtime_iso
        FROM claims
        WHERE scan_run_id = ?
          AND machine_id = ?
          AND file_path = ?
          AND container_path IS NULL
        LIMIT 1
        """,
        (resume_from_run_id, machine_id, file_path),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return str(row[0]), int(row[1]), str(row[2])


def _process_file(
    con: sqlite3.Connection,
    *,
    full: str,
    st: os.stat_result,
    scan_run_id: int,
    machine_id: str,
    ladder: HashLadder,
    resume_from_run_id: int | None,
    include_containers: bool,
    stats: ScanStats,
    now: str,
) -> None:
    """Process one file: resume-cache hit → reuse permanode; otherwise hash,
    upsert permanode, insert claim. Mutates ``stats`` in place. Catches
    per-file errors and increments ``files_errored`` rather than raising —
    one bad file must not abort the whole scan.
    """
    stats.files_walked += 1
    mtime_iso = datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")
    tier, volume = classify_tier(full)

    if resume_from_run_id is not None:
        cached = _resume_cache_lookup(
            con,
            resume_from_run_id=resume_from_run_id,
            machine_id=machine_id,
            file_path=full,
        )
        if cached is not None:
            cached_pid, cached_size, cached_mtime = cached
            if cached_size == int(st.st_size) and cached_mtime == mtime_iso:
                repo_claims.insert(
                    con,
                    permanode_id=cached_pid,
                    machine_id=machine_id,
                    file_path=full,
                    tier=tier,
                    volume=volume,
                    size_bytes=cached_size,
                    scan_run_id=scan_run_id,
                    mtime_iso=mtime_iso,
                )
                stats.files_reused += 1
                stats.permanodes_touched.add(cached_pid)
                return

    try:
        fast = ladder.fast(full)
    except OSError as exc:
        stats.files_errored += 1
        log_swallowed_error("scanner.walker.fast_hash", exc, context={"path": full})
        return

    promote = ladder.should_promote(size_bytes=fast.size_bytes)
    if promote:
        try:
            canonical = ladder.archive(full)
        except OSError as exc:
            stats.files_errored += 1
            log_swallowed_error("scanner.walker.archive_hash", exc, context={"path": full})
            return
    else:
        canonical = fast

    pid = repo_permanodes.upsert(
        con,
        canonical_hash=canonical.hex,
        size_bytes=fast.size_bytes,
        algo=canonical.algo,
    )
    con.execute(
        """
        INSERT OR IGNORE INTO hashes (permanode_id, algo, hex, computed_at)
        VALUES (?, ?, ?, ?)
        """,
        (pid, fast.algo, fast.hex, now),
    )
    if promote:
        con.execute(
            """
            INSERT OR IGNORE INTO hashes (permanode_id, algo, hex, computed_at)
            VALUES (?, ?, ?, ?)
            """,
            (pid, canonical.algo, canonical.hex, now),
        )

    repo_claims.insert(
        con,
        permanode_id=pid,
        machine_id=machine_id,
        file_path=full,
        tier=tier,
        volume=volume,
        size_bytes=fast.size_bytes,
        scan_run_id=scan_run_id,
        mtime_iso=mtime_iso,
    )
    stats.files_hashed += 1
    stats.bytes_hashed += fast.size_bytes
    stats.permanodes_touched.add(pid)

    if include_containers and is_container_path(full):
        cstats = walk_container(
            con,
            container_path=full,
            machine_id=machine_id,
            scan_run_id=scan_run_id,
        )
        stats.containers_walked += cstats.containers_walked
        stats.containers_skipped += cstats.containers_skipped
        stats.containers_errored += cstats.containers_errored
        stats.container_members_walked += cstats.members_walked
        stats.container_members_errored += cstats.members_errored
        stats.bytes_hashed += cstats.bytes_hashed
        stats.permanodes_touched |= cstats.permanodes_touched


def _walk_serial(
    con: sqlite3.Connection,
    root: Path,
    *,
    scan_run_id: int,
    machine_id: str,
    ladder: HashLadder,
    resume_from_run_id: int | None,
    include_containers: bool,
    files_iter: Iterator[tuple[str, os.stat_result]] | None = None,
) -> ScanStats:
    """Walk files via ``_walk_files(root)`` (or an explicit iterator) and
    process each one. The caller owns the connection and the scan_run row.
    """
    stats = ScanStats()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    iterator = files_iter if files_iter is not None else _walk_files(root)
    for full, st in iterator:
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
    return stats


def _loose_files_in(root: Path) -> Iterator[tuple[str, os.stat_result]]:
    """Yield (path, stat) for non-noise files directly under ``root``
    (depth 0 — no recursion). Used by the parallel walker to handle
    files the worker subtrees don't cover."""
    try:
        scanner = os.scandir(root)
    except OSError as exc:
        log_swallowed_error("scanner.walker.scandir", exc, context={"root": str(root)})
        return
    with scanner:
        for entry in scanner:
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError as exc:
                log_swallowed_error(
                    "scanner.walker.is_file", exc, context={"path": entry.path}
                )
                continue
            if is_skipped_file(entry.name):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                log_swallowed_error(
                    "scanner.walker.entry_stat", exc, context={"path": entry.path}
                )
                continue
            yield entry.path, st


def _subtrees_of(root: Path) -> list[str]:
    """Return absolute paths of non-noise top-level subdirs under ``root``."""
    try:
        scanner = os.scandir(root)
    except OSError as exc:
        log_swallowed_error("scanner.walker.scandir", exc, context={"root": str(root)})
        return []
    out: list[str] = []
    with scanner:
        for entry in scanner:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError as exc:
                log_swallowed_error(
                    "scanner.walker.is_dir", exc, context={"path": entry.path}
                )
                continue
            if is_skipped_dir(entry.name):
                continue
            out.append(entry.path)
    return out


class _WorkerResult(TypedDict):
    files_walked: int
    files_hashed: int
    files_reused: int
    files_skipped: int
    files_errored: int
    bytes_hashed: int
    containers_walked: int
    containers_skipped: int
    containers_errored: int
    container_members_walked: int
    container_members_errored: int
    permanodes_touched: set[str]


def _worker_walk_subtree(args: tuple[str, str, int, str, int | None, bool]) -> _WorkerResult:
    """Worker entry point for ``ProcessPoolExecutor``.

    Top-level so the function pickles. Opens its own connection, walks
    one subtree into the parent's ``scan_run_id``, commits, returns
    a pickleable dict of stats.
    """
    db_path_str, subtree_str, scan_run_id, machine_id, resume_from, include_containers = args
    # Import lazily so the worker process doesn't pay the full import cost
    # until it's actually doing work (also avoids any spawn-time issues
    # with module state).
    from steward.infra.db.connect import connect

    con = connect(db_path_str)
    try:
        stats = _walk_serial(
            con,
            Path(subtree_str),
            scan_run_id=scan_run_id,
            machine_id=machine_id,
            ladder=HashLadder(),
            resume_from_run_id=resume_from,
            include_containers=include_containers,
        )
        con.commit()
    finally:
        con.close()
    # ScanStats has a set field that ProcessPoolExecutor can pickle, but
    # collapsing it to a count avoids serialising potentially large hash
    # sets across the IPC boundary.
    return {
        "files_walked": stats.files_walked,
        "files_hashed": stats.files_hashed,
        "files_reused": stats.files_reused,
        "files_skipped": stats.files_skipped,
        "files_errored": stats.files_errored,
        "bytes_hashed": stats.bytes_hashed,
        "containers_walked": stats.containers_walked,
        "containers_skipped": stats.containers_skipped,
        "containers_errored": stats.containers_errored,
        "container_members_walked": stats.container_members_walked,
        "container_members_errored": stats.container_members_errored,
        "permanodes_touched": stats.permanodes_touched,
    }


def _merge_worker_result(stats: ScanStats, result: _WorkerResult) -> None:
    """Fold one worker's result dict into the aggregate ScanStats."""
    stats.files_walked += result["files_walked"]
    stats.files_hashed += result["files_hashed"]
    stats.files_reused += result["files_reused"]
    stats.files_skipped += result["files_skipped"]
    stats.files_errored += result["files_errored"]
    stats.bytes_hashed += result["bytes_hashed"]
    stats.containers_walked += result["containers_walked"]
    stats.containers_skipped += result["containers_skipped"]
    stats.containers_errored += result["containers_errored"]
    stats.container_members_walked += result["container_members_walked"]
    stats.container_members_errored += result["container_members_errored"]
    stats.permanodes_touched |= result["permanodes_touched"]


def _walk_parallel(
    con: sqlite3.Connection,
    db_path: Path,
    root: Path,
    *,
    scan_run_id: int,
    machine_id: str,
    ladder: HashLadder,
    resume_from_run_id: int | None,
    include_containers: bool,
    workers: int,
) -> ScanStats:
    """Subtree-disjoint parallel walk.

    The parent's connection must already have committed the scan_run row
    + scan_start audit (so workers see them through their own
    connections). After all workers complete, the parent walks the loose
    files at the root level (depth 0) on its own connection.
    """
    subtrees = _subtrees_of(root)
    if not subtrees:
        # No subdirs — fall back to serial walk on the parent connection.
        return _walk_serial(
            con,
            root,
            scan_run_id=scan_run_id,
            machine_id=machine_id,
            ladder=ladder,
            resume_from_run_id=resume_from_run_id,
            include_containers=include_containers,
        )

    aggregate = ScanStats()
    work = [
        (
            str(db_path),
            subtree,
            scan_run_id,
            machine_id,
            resume_from_run_id,
            include_containers,
        )
        for subtree in subtrees
    ]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_worker_walk_subtree, w) for w in work]
        for fut in as_completed(futures):
            result = fut.result()
            _merge_worker_result(aggregate, result)

    # Loose files (those directly under root, not in any subdir).
    loose_stats = _walk_serial(
        con,
        root,
        scan_run_id=scan_run_id,
        machine_id=machine_id,
        ladder=ladder,
        resume_from_run_id=resume_from_run_id,
        include_containers=include_containers,
        files_iter=_loose_files_in(root),
    )
    aggregate.files_walked += loose_stats.files_walked
    aggregate.files_hashed += loose_stats.files_hashed
    aggregate.files_reused += loose_stats.files_reused
    aggregate.files_skipped += loose_stats.files_skipped
    aggregate.files_errored += loose_stats.files_errored
    aggregate.bytes_hashed += loose_stats.bytes_hashed
    aggregate.containers_walked += loose_stats.containers_walked
    aggregate.containers_skipped += loose_stats.containers_skipped
    aggregate.containers_errored += loose_stats.containers_errored
    aggregate.container_members_walked += loose_stats.container_members_walked
    aggregate.container_members_errored += loose_stats.container_members_errored
    aggregate.permanodes_touched |= loose_stats.permanodes_touched
    return aggregate


def scan_root(
    *,
    con: sqlite3.Connection,
    root: Path,
    machine_id: str,
    ladder: HashLadder | None = None,
    resume_from_run_id: int | None = None,
    include_containers: bool = False,
    workers: int = 1,
    db_path: Path | None = None,
) -> ScanStats:
    """Walk ``root``, hash each file, insert permanodes + claims.

    The caller owns the DB connection. The scan_start / scan_end audit
    rows bracket the walk.

    When ``workers >= 2``, the walker dispatches each top-level subdir
    of ``root`` to its own process; the parent connection then walks any
    loose files directly under root. ``db_path`` must be supplied in the
    parallel mode because worker processes open their own connections.

    When ``resume_from_run_id`` is set, the walker consults the prior
    run's claims before hashing each file. (Resume works in both serial
    and parallel modes.)
    """
    ladder = ladder or HashLadder()
    if workers >= 2 and db_path is None:
        raise ValueError("scan_root: db_path is required when workers >= 2")

    # Open the scan_run row.
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = con.execute(
        """
        INSERT INTO scan_runs (started_at, finished_at, machine_id, root_path,
                               workers, include_containers, resumed_from, notes)
        VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            machine_id,
            str(root),
            workers,
            1 if include_containers else 0,
            resume_from_run_id,
            "steward scan" + (f" (resume from {resume_from_run_id})" if resume_from_run_id else ""),
        ),
    )
    scan_run_id = int(cur.lastrowid or 0)

    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-scan",
        action="scan_start",
        payload={
            "root": str(root),
            "scan_run_id": scan_run_id,
            "resume_from_run_id": resume_from_run_id,
            "workers": workers,
        },
    )

    if workers >= 2:
        # Workers need to see the scan_run row + scan_start audit, so
        # commit before they spawn. Their own writes follow.
        con.commit()
        assert db_path is not None  # checked above
        stats = _walk_parallel(
            con,
            db_path,
            root,
            scan_run_id=scan_run_id,
            machine_id=machine_id,
            ladder=ladder,
            resume_from_run_id=resume_from_run_id,
            include_containers=include_containers,
            workers=workers,
        )
    else:
        stats = _walk_serial(
            con,
            root,
            scan_run_id=scan_run_id,
            machine_id=machine_id,
            ladder=ladder,
            resume_from_run_id=resume_from_run_id,
            include_containers=include_containers,
        )

    # Close out the scan_run row.
    ts_end = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute(
        """
        UPDATE scan_runs SET finished_at = ?, files_walked = ?, files_hashed = ?,
                             files_skipped = ?, bytes_hashed = ?, errors = ?
        WHERE id = ?
        """,
        (ts_end, stats.files_walked, stats.files_hashed, stats.files_skipped,
         stats.bytes_hashed, stats.files_errored, scan_run_id),
    )
    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-scan",
        action="scan_end",
        payload={
            "root": str(root),
            "scan_run_id": scan_run_id,
            "resume_from_run_id": resume_from_run_id,
            "include_containers": include_containers,
            "files_walked": stats.files_walked,
            "files_hashed": stats.files_hashed,
            "files_reused": stats.files_reused,
            "files_errored": stats.files_errored,
            "bytes_hashed": stats.bytes_hashed,
            "containers_walked": stats.containers_walked,
            "containers_skipped": stats.containers_skipped,
            "containers_errored": stats.containers_errored,
            "container_members_walked": stats.container_members_walked,
            "container_members_errored": stats.container_members_errored,
            "permanodes_touched": len(stats.permanodes_touched),
        },
    )

    return stats
