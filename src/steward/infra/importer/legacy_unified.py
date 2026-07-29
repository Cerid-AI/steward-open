# SPDX-License-Identifier: Apache-2.0

"""Import ``unified-hash.db`` from sprawl-audit into Steward's schema.

The source schema (one ``files`` table, 6.6 M rows on the live Mac Pro)
has one row per ``(path, sha256, container_path)`` observation. Steward
splits that into:

* one **permanode** per ``(canonical_hash, size_bytes)`` — the dedup identity
* one **claim** per ``(machine, path, container_path)`` observation

Because the legacy DB only carries sha256 (blake3 wasn't run yet), we
preserve sha256 in the new ``claims.legacy_sha256`` column AND seed the
permanode's ``canonical_hash`` with it (with ``canonical_hash_algo='sha256'``).
A later ``steward scan --root <tier>`` will compute blake3 for each
permanode and promote ``canonical_hash`` to blake3 in M3+.

The importer is **read-only on the source**. It opens the source DB in
``mode=ro`` and never issues anything but SELECTs. It does NOT touch
sprawl-audit's filesystem or scripts.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from steward.infra.db import repo_audit, repo_claims, repo_meta, repo_permanodes
from steward.infra.db.connect import connect

logger = logging.getLogger("steward.infra.importer.legacy_unified")

# Substrings that are always-skip noise (mirrors sprawl-audit/promote_execute
# NOISE_PATH_SUBSTRINGS — the legacy DB sometimes captured these).
_NOISE_SUBSTRINGS = (
    "/@eaDir/",
    "@SynoResource",
    "@SynoEAStream",
    "/.Spotlight-V100/",
    "/.TemporaryItems/",
    "/.Trashes/",
    "/.fseventsd/",
)


@dataclass(frozen=True)
class ImportSummary:
    rows_read: int
    rows_inserted: int
    rows_skipped: int
    rows_noise_filtered: int
    rows_error_in_source: int
    permanodes_unique: int
    source_path: Path
    source_sha256: str


def _hash_source_db(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    """Return blake3 of the source DB file, for the legacy_import_log row."""
    h = hashlib.sha256()
    # We use sha256 here even though Steward prefers blake3 for inventory
    # content. The source-DB hash is metadata about the import operation,
    # not the inventory itself, and sha256 keeps this dep-free.
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_noise(path: str) -> bool:
    return any(s in path for s in _NOISE_SUBSTRINGS)


def import_legacy(
    source_db: Path,
    target_db: Path,
    *,
    machine_id: str,
    dry_run: bool = False,
    batch_size: int = 5_000,
    limit: int | None = None,
) -> ImportSummary:
    """Import the sprawl-audit ``unified-hash.db`` into ``target_db``.

    The ``target_db`` must already be migrated to schema_version 0001_initial.
    The function never modifies ``source_db``.

    Parameters
    ----------
    source_db
        Path to ``unified-hash.db``. Opened ``mode=ro``.
    target_db
        Path to Steward's ``inventory.db``. Opened read-write.
    machine_id
        Stable id for the machine that originally produced the legacy DB
        observations. v0.3 multi-machine builds carry this through.
    dry_run
        If True, walks the source rows but commits nothing. The summary
        reflects what *would* have been inserted.
    batch_size
        Rows per ``con.commit()`` cycle. 5,000 is a good balance for
        SQLite WAL on a local SSD.
    limit
        Optional ``SELECT ... LIMIT N`` on the source side. Useful for
        the integration-test fixture.
    """
    source = source_db.expanduser()
    target = target_db.expanduser()
    if not source.exists():
        raise FileNotFoundError(f"Source DB not found: {source}")
    if not target.exists():
        raise FileNotFoundError(f"Target DB not found at {target}; run 'steward db migrate' first.")

    source_sha = _hash_source_db(source)
    src_con = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst_con = connect(target)
    try:
        # One scan_run row groups all the claims from this legacy import.
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cur = dst_con.execute(
            """
            INSERT INTO scan_runs (started_at, finished_at, machine_id, root_path, workers,
                                   include_containers, notes)
            VALUES (?, NULL, ?, ?, 0, 1, ?)
            """,
            (ts, machine_id, str(source), "legacy_unified_import"),
        )
        scan_run_id = int(cur.lastrowid or 0)

        # Audit-log the import start.
        repo_audit.append(
            dst_con,
            machine_id=machine_id,
            actor="steward-import-legacy",
            action="legacy_import_start",
            payload={
                "source_db_path": str(source),
                "source_db_sha256": source_sha,
                "scan_run_id": scan_run_id,
                "dry_run": dry_run,
            },
        )

        query = """
            SELECT path, sha256, size_bytes, mtime_iso, error, tier, volume,
                   domain, container_path, container_sha256
            FROM files
        """
        if limit is not None:
            query += f" LIMIT {int(limit)}"

        rows_read = rows_inserted = rows_noise = rows_error = rows_skipped = 0
        seen_permanodes: set[str] = set()

        batch_count = 0
        for row in src_con.execute(query):
            rows_read += 1
            (path, sha256, size_bytes, mtime_iso, error, tier, volume, domain, container_path, container_sha256) = row

            # Source rows with errors carry no usable hash → skip but log
            if error or not sha256 or size_bytes is None:
                rows_error += 1
                continue

            if path and _is_noise(path):
                rows_noise += 1
                continue

            # Insert/touch permanode (canonical_hash=sha256 for now; M3
            # blake3 pass will promote).
            pid = repo_permanodes.upsert(
                dst_con,
                canonical_hash=sha256,
                size_bytes=int(size_bytes),
                algo="sha256",
            )
            seen_permanodes.add(pid)

            # Insert claim. Tier/volume may be NULL in old rows; default to
            # 'unknown'.
            try:
                repo_claims.insert(
                    dst_con,
                    permanode_id=pid,
                    machine_id=machine_id,
                    file_path=path or "",
                    tier=tier or "unknown",
                    volume=volume or "",
                    size_bytes=int(size_bytes),
                    scan_run_id=scan_run_id,
                    domain=domain,
                    container_path=container_path,
                    container_sha256=container_sha256,
                    mtime_iso=mtime_iso,
                    legacy_sha256=sha256,
                )
                rows_inserted += 1
            except sqlite3.IntegrityError:
                # Duplicate (path, container_path, scan_run_id) — the source
                # DB has its own dedup but it's not perfect. Skip.
                rows_skipped += 1
                continue

            batch_count += 1
            if batch_count >= batch_size:
                if dry_run:
                    dst_con.rollback()
                else:
                    dst_con.commit()
                batch_count = 0

        # Final batch flush + scan_run finalisation.
        ts_end = datetime.now(timezone.utc).isoformat(timespec="seconds")
        dst_con.execute(
            """
            UPDATE scan_runs SET finished_at = ?, files_walked = ?, files_hashed = ?,
                                 files_skipped = ?, errors = ?
            WHERE id = ?
            """,
            (ts_end, rows_read, rows_inserted, rows_noise + rows_skipped, rows_error, scan_run_id),
        )

        # legacy_import_log row.
        dst_con.execute(
            """
            INSERT INTO legacy_import_log (imported_at, source_db_path, source_db_sha256,
                                           rows_read, rows_inserted, rows_skipped, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts_end,
                str(source),
                source_sha,
                rows_read,
                rows_inserted,
                rows_noise + rows_skipped + rows_error,
                f"dry_run={dry_run};noise={rows_noise};src_errors={rows_error};dup={rows_skipped}",
            ),
        )

        # Audit-log the import end.
        repo_audit.append(
            dst_con,
            machine_id=machine_id,
            actor="steward-import-legacy",
            action="legacy_import_end",
            payload={
                "scan_run_id": scan_run_id,
                "rows_read": rows_read,
                "rows_inserted": rows_inserted,
                "rows_skipped": rows_noise + rows_skipped,
                "rows_error_in_source": rows_error,
                "permanodes_unique": len(seen_permanodes),
                "dry_run": dry_run,
            },
        )

        if dry_run:
            dst_con.rollback()
        else:
            repo_meta.set_(dst_con, "last_legacy_import_at", ts_end)
            dst_con.commit()

        return ImportSummary(
            rows_read=rows_read,
            rows_inserted=rows_inserted,
            rows_skipped=rows_noise + rows_skipped,
            rows_noise_filtered=rows_noise,
            rows_error_in_source=rows_error,
            permanodes_unique=len(seen_permanodes),
            source_path=source,
            source_sha256=source_sha,
        )
    finally:
        src_con.close()
        dst_con.close()
