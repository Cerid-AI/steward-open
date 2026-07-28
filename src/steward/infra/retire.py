# SPDX-License-Identifier: Apache-2.0

"""Direct-retire action — rm-in-place for cloud-FP-backed tiers (ADR-0014 / 0015).

Used by the ``retire_direct`` manifest action. Unlike ``stash``,
which does a same-FS rename into a cooling-off dir, ``retire_direct``
calls :func:`pathlib.Path.unlink` on the resolved unlink path. The
caller (typically apply) wraps the call in the apply transaction so
the file removal and the audit row commit together.

This is the right pattern for tiers backed by an external sync
agent that already has a deletion-recovery mechanism — Dropbox FP
(cloud trash / version history — window is account-specific: e.g.
Dropbox 30 d base or 1 yr with Extended Version History; iCloud
Drive Deleted Items), etc. For those
tiers a same-FS stash rename is wrong (the sync agent sees two
events and "retires" the file by uploading it to a fresh cloud
path). Direct unlink is the right semantic.

**ADR-0015 path policy:** for Dropbox FP paths, verify prefers the
store materialization (reliable stats) and unlink prefers the
user-facing mount (cloud propagation). Opt out with
``prefer_mount_unlink=False`` (CLI: ``--allow-store-path-unlink``).

See ADR-0014 + ADR-0015 for the full reasoning + decision context.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from steward.core.errors import FPUnavailableError, ManifestError
from steward.core.fp_paths import claim_path_aliases, resolve_fp_paths
from steward.core.hashing import hash_file_by_algo
from steward.infra.db import repo_audit

logger = logging.getLogger("steward.infra.retire")


def _hash_file(path: Path, *, algo: str, chunk_size: int = 1 << 20) -> str:
    """Thin wrapper over :func:`steward.core.hashing.hash_file_by_algo`.

    Retained for clarity within this module; new code that needs to
    hash-and-verify should call the shared helper directly.
    """
    hex_d, _size = hash_file_by_algo(path, algo=algo, chunk_size=chunk_size)
    return hex_d


def _existing_other_claims(
    con: sqlite3.Connection,
    *,
    permanode_id: str,
    excluded_paths: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Return current claims for ``permanode_id`` outside ``excluded_paths``.

    Used as a sanity guard: a ``retire_direct`` row that would remove
    the LAST remaining copy of a permanode is surfaced as a warning
    in the audit payload (not refused — operator-in-the-loop trumps
    the heuristic).
    """
    if not excluded_paths:
        excluded_paths = ("",)
    placeholders = ",".join("?" for _ in excluded_paths)
    rows = con.execute(
        f"""
        SELECT id, tier, file_path, machine_id
        FROM claims
        WHERE permanode_id = ? AND is_current = 1
          AND file_path NOT IN ({placeholders})
        ORDER BY id ASC
        """,
        (permanode_id, *excluded_paths),
    ).fetchall()
    return [
        {
            "claim_id": int(r[0]),
            "tier": str(r[1]),
            "file_path": str(r[2]),
            "machine_id": str(r[3]),
        }
        for r in rows
    ]


def retire_direct(
    *,
    con: sqlite3.Connection,
    source_path: Path,
    permanode_id: str,
    expected_canonical_hash: str,
    expected_size_bytes: int,
    manifest_run_id: str,
    machine_id: str,
    rationale: str,
    cooling_off_mechanism: str,
    dry_run: bool,
    verify: bool = True,
    prefer_mount_unlink: bool = True,
) -> dict[str, Any]:
    """Verify + rm the resolved unlink path; append audit row.

    Parameters mirror :func:`steward.infra.stash.same_fs_rename_to_stash`
    plus the ``cooling_off_mechanism`` string (operator-supplied;
    typically ``"dropbox-cloud-trash-30d"`` or similar — names where
    recovery lives so a forensic audit-log walker can find it).

    When ``verify=True`` (default):

    1. Resolve FP store/mount paths (ADR-0015).
    2. Existence + regular-file on the verify path (store preferred).
    3. Size + canonical-algo hash match expectations.

    When ``verify=False`` (F11 — bulk retire trusting inventory):

    Only the existence + regular-file checks run. The hash + size
    verification is SKIPPED — the caller is asserting that Steward's
    recorded canonical_hash for this permanode is trustworthy and
    that the file on disk has not changed since the scan.

    ``prefer_mount_unlink`` (default True): Dropbox claim paths unlink
    via the user-facing mount. Set False to unlink the claim path as
    written (local-only reclaim; cloud propagation not guaranteed).

    On dry-run: any verification runs but no unlink. Returns the planned payload.
    On execute: ``Path.unlink()`` + audit-row append + claim is_current → 0.
    """
    claim_str = str(source_path)
    resolution = resolve_fp_paths(
        claim_str, prefer_mount_unlink=prefer_mount_unlink
    )
    # Logic law: verify and unlink are the same path (ADR-0015 amended).
    op_path = Path(resolution.unlink_path)
    if Path(resolution.verify_path) != op_path:
        raise ManifestError(
            "retire_direct: internal error — verify_path != unlink_path "
            f"({resolution.verify_path!r} vs {resolution.unlink_path!r})"
        )
    aliases = claim_path_aliases(claim_str)

    try:
        present = op_path.exists()
    except OSError as exc:
        if resolution.used_mount_for_unlink:
            raise FPUnavailableError(
                f"retire_direct: File Provider stat failed for {op_path} "
                f"({exc}); retry once the FP has settled"
            ) from exc
        raise ManifestError(
            f"retire_direct: cannot stat {op_path}: {exc}"
        ) from exc

    if not present:
        if resolution.used_mount_for_unlink and claim_str != str(op_path):
            # Store-only inventory is common; mount missing → cannot do
            # cloud-propagating delete without guessing a forked twin.
            raise ManifestError(
                f"retire_direct: mount path missing for cloud-propagating "
                f"unlink: {op_path} (claim was {claim_str}). Re-scan the "
                f"CloudStorage mount root, or re-run with "
                f"--allow-store-path-unlink for local-only reclaim "
                f"(cloud trash / quota not guaranteed — ADR-0015)."
            )
        raise ManifestError(f"retire_direct: source not found: {op_path}")
    if not op_path.is_file():
        raise ManifestError(
            f"retire_direct: source is not a regular file: {op_path}"
        )

    check_path = op_path
    unlink_path = op_path

    algo: str | None = None
    if verify:
        actual_size = check_path.stat().st_size
        if actual_size != expected_size_bytes:
            raise ManifestError(
                f"retire_direct: size mismatch for {check_path}: "
                f"expected {expected_size_bytes} got {actual_size}"
            )
        algo_row = con.execute(
            "SELECT canonical_hash_algo FROM permanodes WHERE id = ?",
            (permanode_id,),
        ).fetchone()
        algo = str(algo_row[0]) if algo_row is not None else "blake3"
        actual_hash = _hash_file(check_path, algo=algo)
        if actual_hash != expected_canonical_hash:
            raise ManifestError(
                f"retire_direct: hash mismatch for {check_path}: "
                f"expected {expected_canonical_hash[:16]}… "
                f"got {actual_hash[:16]}… ({algo})"
            )

    other_claims = _existing_other_claims(
        con,
        permanode_id=permanode_id,
        excluded_paths=aliases,
    )
    last_copy = not other_claims

    payload: dict[str, Any] = {
        "source_path": claim_str,
        "verify_path": str(check_path),
        "unlink_path": str(unlink_path),
        "used_mount_for_unlink": resolution.used_mount_for_unlink,
        "fp_tier_hint": resolution.tier_hint,
        "canonical_hash": expected_canonical_hash,
        "size_bytes": expected_size_bytes,
        "rationale": rationale,
        "cooling_off_mechanism": cooling_off_mechanism,
        "other_claims_count": len(other_claims),
        "last_copy_warning": last_copy,
        "verified": verify,
        "verify_algo": algo,
        "dry_run": dry_run,
    }
    if other_claims:
        payload["sample_other_claims"] = other_claims[:5]

    resolved_permanode_id: str | None = permanode_id
    row = con.execute(
        "SELECT 1 FROM permanodes WHERE id = ?", (permanode_id,)
    ).fetchone()
    if row is None:
        payload["manifest_permanode_id"] = permanode_id
        resolved_permanode_id = None

    if dry_run:
        return payload

    # ── execute ─────────────────────────────────────────────────
    try:
        unlink_path.unlink()
    except TimeoutError as exc:
        raise FPUnavailableError(
            f"retire_direct: File Provider delete timed out for {unlink_path} "
            f"({exc}); retry once the FP has settled"
        ) from exc
    except FileNotFoundError as exc:
        # Race: existed at check time, gone at unlink — treat as error.
        raise ManifestError(
            f"retire_direct: unlink path disappeared before delete: {unlink_path}"
        ) from exc

    placeholders = ",".join("?" for _ in aliases)
    con.execute(
        f"""
        UPDATE claims SET is_current = 0
        WHERE permanode_id = ? AND is_current = 1
          AND file_path IN ({placeholders})
        """,
        (permanode_id, *aliases),
    )
    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-apply",
        action="retire_direct_executed",
        payload=payload,
        manifest_run_id=manifest_run_id,
        permanode_id=resolved_permanode_id,
    )
    return payload


__all__ = ["retire_direct"]
