# SPDX-License-Identifier: Apache-2.0

"""Promote action — copy source → destination with hash verification.

The promote action is the cross-tier counterpart to stash:

* ``stash``   — same-FS rename (always reversible, never crosses devices)
* ``promote`` — copy with hash-verify (crosses devices on purpose; e.g.
  Backup NAS → live L2 SSD)

Idempotency rules:

* destination missing → copy + verify + audit ``promote_committed``
* destination present, size matches, hash matches → audit ``promote_skipped`` (skip-ok)
* destination present, size matches, hash mismatch → audit ``promote_mismatch`` (NEVER overwrite)
* destination present, size mismatch → audit ``promote_mismatch`` (NEVER overwrite)

Crash safety: write to ``<dst>.inflight``, fsync, ``os.rename`` to final.
Aborts past the partial write leave the ``.inflight`` file behind (cleaned
up by ``steward apply`` start-of-row pre-check) but never touch ``<dst>``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from steward.core.errors import ManifestError
from steward.infra.db import repo_audit
from steward.infra.observability import log_swallowed_error

logger = logging.getLogger("steward.infra.promote")

_HASH_CHUNK = 8 * 1024 * 1024  # 8 MiB streaming chunks


def _hash_file_blake3(path: Path) -> tuple[str, int]:
    """Return ``(blake3_hex, size_bytes)`` for ``path``.

    Kept for back-compat with code paths that explicitly want blake3;
    new code should prefer
    :func:`steward.core.hashing.hash_file_by_algo` which honours the
    permanode's recorded algo (blake3, xxh3_128, sha256).
    """
    from steward.core.hashing import hash_file_by_algo

    return hash_file_by_algo(path, algo="blake3", chunk_size=_HASH_CHUNK)


def _hash_file_by_permanode(con: sqlite3.Connection, permanode_id: str, path: Path) -> tuple[str, int, str]:
    """Look up the permanode's recorded algo and hash ``path`` accordingly.

    Returns ``(hex_digest, size_bytes, algo)``. Falls back to blake3
    when the permanode isn't found in the DB (synthetic test rows /
    hand-built manifests).
    """
    from steward.core.hashing import hash_file_by_algo

    row = con.execute(
        "SELECT canonical_hash_algo FROM permanodes WHERE id = ?",
        (permanode_id,),
    ).fetchone()
    algo = str(row[0]) if row is not None else "blake3"
    hex_d, size = hash_file_by_algo(path, algo=algo, chunk_size=_HASH_CHUNK)
    return (hex_d, size, algo)


def _resolve_permanode_id(con: sqlite3.Connection, candidate: str) -> str | None:
    """Return ``candidate`` iff it exists in permanodes; else None."""
    row = con.execute(
        "SELECT 1 FROM permanodes WHERE id = ?",
        (candidate,),
    ).fetchone()
    return candidate if row else None


def promote_with_verify(
    *,
    con: sqlite3.Connection,
    source_path: Path,
    destination_path: Path,
    expected_canonical_hash: str,
    expected_size_bytes: int,
    permanode_id: str,
    manifest_run_id: str,
    machine_id: str,
    actor: str = "steward-apply",
    rationale: str = "",
    dry_run: bool = False,
    preserve_mtime: bool = True,
) -> tuple[str, Path]:
    """Promote ``source_path`` → ``destination_path`` with hash verification.

    The ``expected_*`` parameters come from the manifest row. We re-hash
    the source post-copy and compare against ``expected_canonical_hash``
    to ensure (a) the file hasn't changed since the plan was produced
    and (b) the copy didn't corrupt the bytes.

    Returns ``(disposition, destination)`` where disposition is one of:
    ``committed``, ``skipped_ok``, ``mismatch``, ``planned`` (dry-run).
    """
    src = source_path.resolve()
    dst = destination_path.expanduser()
    if not src.exists():
        raise ManifestError(f"promote: source missing: {src}")

    resolved_pid = _resolve_permanode_id(con, permanode_id)
    audit_payload_base = {
        "source": str(src),
        "destination": str(dst),
        "expected_canonical_hash": expected_canonical_hash,
        "expected_size_bytes": expected_size_bytes,
        "rationale": rationale,
        "dry_run": dry_run,
    }
    if resolved_pid is None:
        audit_payload_base["manifest_permanode_id"] = permanode_id

    # Idempotency check: if dst already exists, decide skip vs mismatch.
    if dst.exists():
        try:
            existing_hash, existing_size, _algo = _hash_file_by_permanode(con, permanode_id, dst)
        except OSError as exc:
            log_swallowed_error("promote.hash_existing", exc, context={"dst": str(dst)})
            raise ManifestError(f"promote: cannot hash existing destination: {exc}") from exc

        if existing_size == expected_size_bytes and existing_hash == expected_canonical_hash:
            disposition = "skipped_ok"
            repo_audit.append(
                con,
                machine_id=machine_id,
                actor=actor,
                action="promote_skipped",
                payload={
                    **audit_payload_base,
                    "disposition": disposition,
                    "existing_hash": existing_hash,
                    "existing_size": existing_size,
                },
                permanode_id=resolved_pid,
                manifest_run_id=manifest_run_id,
            )
            return (disposition, dst)
        # size or hash mismatch — never overwrite
        repo_audit.append(
            con,
            machine_id=machine_id,
            actor=actor,
            action="promote_mismatch",
            payload={
                **audit_payload_base,
                "existing_hash": existing_hash,
                "existing_size": existing_size,
            },
            permanode_id=resolved_pid,
            manifest_run_id=manifest_run_id,
        )
        return ("mismatch", dst)

    # Dry-run: verify source, audit, no FS write
    if dry_run:
        src_hash, src_size, _algo = _hash_file_by_permanode(con, permanode_id, src)
        ok = (src_hash == expected_canonical_hash) and (src_size == expected_size_bytes)
        repo_audit.append(
            con,
            machine_id=machine_id,
            actor=actor,
            action="promote_planned",
            payload={
                **audit_payload_base,
                "source_hash": src_hash,
                "source_size": src_size,
                "would_succeed": ok,
            },
            permanode_id=resolved_pid,
            manifest_run_id=manifest_run_id,
        )
        return ("planned", dst)

    # Execute: copy → fsync → verify → rename
    # The single-pass copy-and-hash uses an algo-aware hasher so the
    # computed hash matches the permanode's recorded canonical_hash_algo
    # (blake3 / xxh3_128 / sha256).
    dst.parent.mkdir(parents=True, exist_ok=True)
    inflight = dst.with_suffix(dst.suffix + ".inflight")
    try:
        from steward.core.hashing import new_hasher_for

        algo_row = con.execute(
            "SELECT canonical_hash_algo FROM permanodes WHERE id = ?",
            (permanode_id,),
        ).fetchone()
        algo = str(algo_row[0]) if algo_row is not None else "blake3"
        h = new_hasher_for(algo)
        copied_size = 0
        with src.open("rb") as fin, inflight.open("wb") as fout:
            while True:
                chunk = fin.read(_HASH_CHUNK)
                if not chunk:
                    break
                fout.write(chunk)
                h.update(chunk)  # type: ignore[attr-defined]
                copied_size += len(chunk)
            fout.flush()
            os.fsync(fout.fileno())
        copied_hash = h.hexdigest()  # type: ignore[attr-defined]
        if copied_hash != expected_canonical_hash or copied_size != expected_size_bytes:
            inflight.unlink(missing_ok=True)
            repo_audit.append(
                con,
                machine_id=machine_id,
                actor=actor,
                action="promote_mismatch",
                payload={
                    **audit_payload_base,
                    "copied_hash": copied_hash,
                    "copied_size": copied_size,
                    "stage": "post-copy",
                },
                permanode_id=resolved_pid,
                manifest_run_id=manifest_run_id,
            )
            return ("mismatch", dst)
        if preserve_mtime:
            src_stat = src.stat()
            os.utime(inflight, ns=(src_stat.st_atime_ns, src_stat.st_mtime_ns))
        os.rename(inflight, dst)
    except OSError as exc:
        # Best-effort cleanup; never raise from the cleanup itself.
        try:
            inflight.unlink(missing_ok=True)
        except Exception as cleanup_exc:  # noqa: BLE001 — cleanup must not mask the original error
            log_swallowed_error(
                "promote.inflight_cleanup",
                cleanup_exc,
                context={"inflight": str(inflight)},
            )
        log_swallowed_error(
            "promote.copy",
            exc,
            context={"src": str(src), "dst": str(dst)},
        )
        raise

    repo_audit.append(
        con,
        machine_id=machine_id,
        actor=actor,
        action="promote_committed",
        payload={
            **audit_payload_base,
            "copied_hash": copied_hash,
            "copied_size": copied_size,
            "committed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        permanode_id=resolved_pid,
        manifest_run_id=manifest_run_id,
    )
    return ("committed", dst)
