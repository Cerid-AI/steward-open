# SPDX-License-Identifier: Apache-2.0

"""Apply a manifest — dry-run or execute.

Supported actions: ``stash``, ``promote``, ``retire_direct`` (ADR-0014/0015),
``nas_manifest`` (export for operator/DSM; no NAS FS mutation).

CLI-only lifecycle steps (``restore``, ``finalize_stash``, ``reclassify``)
error with guidance rather than silent skip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from steward.core.errors import EstateError, FPUnavailableError, ManifestError
from steward.core.manifest_io import read_manifest
from steward.infra.db import repo_audit
from steward.infra.db.connect import connect
from steward.infra.db.settings import inventory_db_path
from steward.infra.estate.guard import OwnershipGuard, load_guard, manifest_checks
from steward.infra.nas_manifest import record_nas_manifest_row
from steward.infra.promote import promote_with_verify
from steward.infra.stash import same_fs_rename_to_stash

if TYPE_CHECKING:
    from steward.core.model.manifest import Manifest

logger = logging.getLogger("steward.infra.db.apply")

# Actions that belong to other CLI surfaces, not apply dispatch.
_CLI_ONLY_ACTIONS: dict[str, str] = {
    "restore": "use `steward stash restore --run-id <id>`",
    "finalize_stash": "use `steward stash finalize --run-id <id>`",
    "reclassify": "use `steward classify`",
}


@dataclass
class ApplyResult:
    manifest_run_id: str
    rows_total: int
    rows_applied: int
    rows_skipped: int
    rows_errored: int
    dry_run: bool
    errors: list[str] = field(default_factory=list)
    rejected_imported_claims: list[str] = field(default_factory=list)
    """Manifest row indices refused by the cross-machine pre-flight check
    (ADR-0013). When non-empty the apply was rejected and zero rows
    were attempted."""
    nas_export_path: str | None = None
    """Set when one or more ``nas_manifest`` rows were recorded (execute)."""
    rejected_foreign_volume: list[str] = field(default_factory=list)
    """Refusals from the volume-ownership pre-flight: rows on volumes this
    host may not act on (estate ``enforce``), or an unknown host / an
    inventory.db bound to another machine. When non-empty the apply was
    rejected and zero rows were attempted."""


class ApplyRefused(Exception):
    """Raised by :func:`apply_manifest` when a pre-flight refuses.

    The exception's ``result`` attribute carries the partial
    :class:`ApplyResult` with ``rejected_imported_claims`` or
    ``rejected_foreign_volume`` populated.
    """

    def __init__(self, result: "ApplyResult", message: str | None = None) -> None:
        super().__init__(
            message
            or f"apply rejected: {len(result.rejected_imported_claims)} row(s) reference attached-only permanodes"
        )
        self.result = result

    @property
    def rejected(self) -> list[str]:
        return [*self.result.rejected_imported_claims, *self.result.rejected_foreign_volume]


def apply_manifest(
    *,
    manifest_path: Path,
    machine_id: str,
    dry_run: bool,
    max_files: int | None = None,
    skip_verify: bool = False,
    prefer_mount_unlink: bool = True,
) -> ApplyResult:
    """Apply ``manifest_path`` against the configured inventory DB.

    Runs the cross-machine pre-flight (ADR-0013) before any row work.
    If the pre-flight rejects any row, raises :class:`ApplyRefused`
    with a :class:`ApplyResult` whose ``rejected_imported_claims``
    field carries the human-readable rejections. The
    ``apply_rejected_imported_claim`` audit row is written in the
    same transaction as the refusal so the chain captures the event.

    ``skip_verify`` (default False) propagates to ``retire_direct``
    rows: when True, the per-file hash + size verification is
    SKIPPED for retire_direct (only the existence check runs). Other
    actions (stash, promote) ignore the flag. See ADR-0014 for the
    safety trade-off; use only when the inventory's recorded hash is
    trusted and the post-action cooling-off (e.g. cloud trash) is
    sufficient recovery.

    ``prefer_mount_unlink`` (default True, ADR-0015): Dropbox FP
    paths unlink via the user-facing mount. Set False
    (``--allow-store-path-unlink``) for local-only reclaim.
    """
    from steward.infra.sync.apply_preflight import preflight_apply

    manifest = read_manifest(manifest_path)

    guard = load_guard(db_path=inventory_db_path())
    if guard is not None:
        _ownership_preflight(guard, manifest=manifest, machine_id=machine_id, dry_run=dry_run)

    # ── Pre-flight FIRST. Uses its own connection so we never leak an
    # attach into the apply transaction. ──
    preflight = preflight_apply(manifest=manifest, machine_id=machine_id)
    if not preflight.ok:
        return _record_preflight_rejection(
            manifest=manifest,
            machine_id=machine_id,
            preflight=preflight,
            dry_run=dry_run,
        )

    con = connect(inventory_db_path())
    try:
        return _apply_with_con(
            con,
            manifest,
            machine_id,
            dry_run,
            max_files,
            skip_verify=skip_verify,
            prefer_mount_unlink=prefer_mount_unlink,
        )
    finally:
        con.close()


def _ownership_preflight(guard: OwnershipGuard, *, manifest: Manifest, machine_id: str, dry_run: bool) -> None:
    """Check every row's source and destination against volume ownership.

    Runs on dry-run too. ``report`` mode audits ``ownership_would_refuse``
    and returns; ``enforce`` audits ``apply_rejected_foreign_volume`` and
    raises :class:`ApplyRefused` before any row is attempted.
    """
    run_id = manifest.header.manifest_run_id

    def refused(messages: list[str], message: str) -> ApplyRefused:
        result = ApplyResult(
            manifest_run_id=run_id,
            rows_total=len(manifest.rows),
            rows_applied=0,
            rows_skipped=0,
            rows_errored=0,
            dry_run=dry_run,
            rejected_foreign_volume=messages,
        )
        return ApplyRefused(result, message)

    try:
        guard.require_identity()
    except EstateError as exc:
        raise refused([str(exc)], f"apply rejected: {exc}") from exc

    refusals = guard.refusals(manifest_checks(manifest.rows))
    if not refusals:
        return
    guard.record(
        inventory_db_path(),
        refusals,
        refusal_action="apply_rejected_foreign_volume",
        machine_id=machine_id,
        actor="steward-apply",
        manifest_run_id=run_id,
        context={"dry_run": dry_run},
    )
    if guard.enforcing:
        rows = len({r.check.ref for r in refusals})
        raise refused(
            [r.describe() for r in refusals],
            f"apply rejected: {rows} row(s) touch volumes host {guard.identity.host_id!r} may not change",
        )


def _record_preflight_rejection(
    *,
    manifest: object,
    machine_id: str,
    preflight: object,
    dry_run: bool,
) -> ApplyResult:
    """Append rejection audit rows + raise :class:`ApplyRefused`.

    The audit rows live in their own transaction; the apply
    transaction never opens. One row per rejection so a forensic
    grep can find every refused permanode.
    """
    from steward.core.model.manifest import Manifest
    from steward.infra.sync.apply_preflight import ApplyPreflightReport

    assert isinstance(manifest, Manifest)
    assert isinstance(preflight, ApplyPreflightReport)

    messages: list[str] = []
    con = connect(inventory_db_path())
    try:
        for r in preflight.rejections:
            messages.append(f"row {r.row_index}: permanode {r.permanode_id[:16]}… ({r.source_path}) — {r.reason}")
            # Note: ``permanode_id`` is intentionally NOT passed as a
            # separate column — the audit_log row has a FK to local
            # permanodes, and the rejected permanode by definition
            # doesn't exist locally. The full id lives in the payload
            # for forensic grep.
            repo_audit.append(
                con,
                machine_id=machine_id,
                actor="steward-apply",
                action="apply_rejected_imported_claim",
                payload={
                    "row_index": r.row_index,
                    "permanode_id": r.permanode_id,
                    "source_path": r.source_path,
                    "reason": r.reason,
                    "found_in_machine_id": r.found_in_machine_id,
                    "dry_run": dry_run,
                },
                manifest_run_id=manifest.header.manifest_run_id,
            )
        con.commit()
    finally:
        con.close()

    result = ApplyResult(
        manifest_run_id=manifest.header.manifest_run_id,
        rows_total=len(manifest.rows),
        rows_applied=0,
        rows_skipped=0,
        rows_errored=0,
        dry_run=dry_run,
        rejected_imported_claims=messages,
    )
    raise ApplyRefused(result)


def _apply_with_con(
    con: object,
    manifest: object,  # Manifest (kept untyped to avoid heavy import in module-level)
    machine_id: str,
    dry_run: bool,
    max_files: int | None,
    *,
    skip_verify: bool = False,
    prefer_mount_unlink: bool = True,
) -> ApplyResult:
    from steward.core.model.manifest import Manifest
    from steward.infra.retire import retire_direct as _retire

    assert isinstance(manifest, Manifest)
    import sqlite3

    assert isinstance(con, sqlite3.Connection)

    result = ApplyResult(
        manifest_run_id=manifest.header.manifest_run_id,
        rows_total=len(manifest.rows),
        rows_applied=0,
        rows_skipped=0,
        rows_errored=0,
        dry_run=dry_run,
    )

    # Audit the apply-bracket start.
    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-apply",
        action="apply_start",
        payload={
            "manifest_path_resolved": True,
            "policy_name": manifest.header.policy_name,
            "phase_name": manifest.header.phase_name,
            "rows_total": result.rows_total,
            "dry_run": dry_run,
            "max_files": max_files,
            "prefer_mount_unlink": prefer_mount_unlink,
            "skip_verify": skip_verify,
        },
        manifest_run_id=manifest.header.manifest_run_id,
    )

    for i, row in enumerate(manifest.rows):
        if max_files is not None and result.rows_applied >= max_files:
            result.rows_skipped += 1
            continue
        try:
            if row.action == "stash":
                if not row.destination_path:
                    raise ManifestError(f"stash row {i} missing destination_path")
                same_fs_rename_to_stash(
                    con=con,
                    source_path=Path(row.source_path),
                    destination_path=Path(row.destination_path),
                    permanode_id=row.permanode_id,
                    manifest_run_id=manifest.header.manifest_run_id,
                    machine_id=machine_id,
                    rationale=row.rationale,
                    dry_run=dry_run,
                )
                result.rows_applied += 1
            elif row.action == "promote":
                if not row.destination_path:
                    raise ManifestError(f"promote row {i} missing destination_path")
                promote_with_verify(
                    con=con,
                    source_path=Path(row.source_path),
                    destination_path=Path(row.destination_path),
                    expected_canonical_hash=row.canonical_hash,
                    expected_size_bytes=row.size_bytes,
                    permanode_id=row.permanode_id,
                    manifest_run_id=manifest.header.manifest_run_id,
                    machine_id=machine_id,
                    rationale=row.rationale,
                    dry_run=dry_run,
                )
                result.rows_applied += 1
            elif row.action == "retire_direct":
                # ADR-0014 + ADR-0015: rm-in-place; mount preferred for FP.
                _retire(
                    con=con,
                    source_path=Path(row.source_path),
                    permanode_id=row.permanode_id,
                    expected_canonical_hash=row.canonical_hash,
                    expected_size_bytes=row.size_bytes,
                    manifest_run_id=manifest.header.manifest_run_id,
                    machine_id=machine_id,
                    rationale=row.rationale,
                    cooling_off_mechanism=row.destination_tier or "unspecified",
                    dry_run=dry_run,
                    verify=not skip_verify,
                    prefer_mount_unlink=prefer_mount_unlink,
                )
                result.rows_applied += 1
            elif row.action == "nas_manifest":
                export = record_nas_manifest_row(
                    con=con,
                    row=row,
                    row_index=i,
                    manifest_run_id=manifest.header.manifest_run_id,
                    machine_id=machine_id,
                    dry_run=dry_run,
                )
                result.rows_applied += 1
                if not dry_run:
                    result.nas_export_path = str(export.export_path)
            elif row.action in _CLI_ONLY_ACTIONS:
                result.rows_errored += 1
                result.errors.append(
                    f"row {i}: action {row.action!r} is not applied via "
                    f"`steward apply` — {_CLI_ONLY_ACTIONS[row.action]}"
                )
            else:
                result.rows_errored += 1
                result.errors.append(f"row {i}: action {row.action!r} is not supported by apply")
        except FPUnavailableError as exc:
            # Cloud-FP tier congested/degraded — defer this row (no writes
            # happened for it) and keep the batch going. Retry on a settled FP.
            result.rows_errored += 1
            result.errors.append(f"row {i}: FP unavailable (retry later): {exc}")
        except TimeoutError as exc:
            # Belt-and-suspenders: raw Errno 60 that escaped retire_direct
            # (should be rare after FP timeout mapping). Defer row; do not
            # abort the whole apply batch.
            result.rows_errored += 1
            result.errors.append(f"row {i}: FP unavailable (retry later): {exc}")
        except ManifestError as exc:
            result.rows_errored += 1
            result.errors.append(f"row {i}: {exc}")

    # Audit the apply-bracket end (always recorded — even on dry-run).
    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-apply",
        action="apply_end",
        payload={
            "rows_total": result.rows_total,
            "rows_applied": result.rows_applied,
            "rows_skipped": result.rows_skipped,
            "rows_errored": result.rows_errored,
            "dry_run": dry_run,
            "nas_export_path": result.nas_export_path,
        },
        manifest_run_id=manifest.header.manifest_run_id,
    )

    if dry_run:
        con.rollback()
    else:
        con.commit()
    return result
