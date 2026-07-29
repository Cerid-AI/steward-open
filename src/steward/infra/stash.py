# SPDX-License-Identifier: Apache-2.0

"""Same-FS rename to a cooling-off stash + audit-log integration.

ADR-0007: every "retire from a live tier" mutation is a rename into
``<tier>/_cooling-off-stash/<manifest_run_id>/...``. Cooling-off lets
the operator review + reverse before the final ``rm`` runs via
``steward stash finalize``.

This module is the *executor* of the ``stash`` manifest action; the
plan generator that picks paths to stash is the M5 reconciler.
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

logger = logging.getLogger("steward.infra.stash")


def same_fs_rename_to_stash(
    *,
    con: sqlite3.Connection,
    source_path: Path,
    destination_path: Path,
    permanode_id: str,
    manifest_run_id: str,
    machine_id: str,
    actor: str = "steward-apply",
    rationale: str = "",
    dry_run: bool = False,
) -> tuple[Path, Path]:
    """Atomically rename ``source_path`` to ``destination_path``.

    Both paths must be on the same filesystem (precondition checked via
    ``os.stat(...).st_dev``). The destination's parent dir is created
    if it doesn't exist. An audit row is appended in the caller's
    transaction.

    Dry-run mode performs all the checks (existence, same-FS, no overwrite)
    and appends a ``stash_planned`` audit row but performs no rename.

    Returns ``(actual_source, actual_destination)`` as Paths.
    """
    src = source_path.resolve()
    dst = destination_path.expanduser()
    if not src.exists():
        raise ManifestError(f"stash: source missing: {src}")
    if dst.exists():
        raise ManifestError(f"stash: destination already exists: {dst}")

    src_dev = src.stat().st_dev
    dst_parent = dst.parent
    if dst_parent.exists():
        dst_dev = dst_parent.stat().st_dev
    else:
        # Walk up until we hit an existing parent to determine device.
        anc = dst_parent
        while not anc.exists() and anc != anc.parent:
            anc = anc.parent
        dst_dev = anc.stat().st_dev
    if src_dev != dst_dev:
        raise ManifestError(f"stash: cross-FS rename refused ({src} ↦ {dst}); same-FS only.")

    action = "stash_planned" if dry_run else "stash_committed"
    payload = {
        "source": str(src),
        "destination": str(dst),
        "rationale": rationale,
        "dry_run": dry_run,
    }
    # Resolve the manifest-supplied permanode_id against the DB. If the
    # row doesn't exist (hand-built manifest, stale plan, etc.) keep the
    # id in payload_json for traceability but null the FK column so the
    # audit insert doesn't violate the foreign-key constraint.
    resolved_permanode_id: str | None = permanode_id
    row = con.execute("SELECT 1 FROM permanodes WHERE id = ?", (permanode_id,)).fetchone()
    if row is None:
        payload["manifest_permanode_id"] = permanode_id
        resolved_permanode_id = None

    if dry_run:
        repo_audit.append(
            con,
            machine_id=machine_id,
            actor=actor,
            action=action,
            payload=payload,
            permanode_id=resolved_permanode_id,
            manifest_run_id=manifest_run_id,
        )
        return (src, dst)

    dst_parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dst)
    except OSError as exc:
        log_swallowed_error(
            "stash.same_fs_rename",
            exc,
            context={"src": str(src), "dst": str(dst)},
        )
        raise

    repo_audit.append(
        con,
        machine_id=machine_id,
        actor=actor,
        action=action,
        payload={
            **payload,
            "renamed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        permanode_id=resolved_permanode_id,
        manifest_run_id=manifest_run_id,
    )
    return (src, dst)
