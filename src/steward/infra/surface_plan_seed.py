# SPDX-License-Identifier: Apache-2.0
"""Surface selection → plan-seed TSV (ADR-0022 Wave C; operator-gated).

Writes a dry plan skeleton from current claims under a path_prefix.
Never executes apply. Optional dual-presence filter for retire_direct rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from steward._version import __version__
from steward.core.manifest_io import write_manifest
from steward.core.model.manifest import Manifest, ManifestHeader, ManifestRow
from steward.infra.db.connect import connect

SeedAction = Literal["retire_direct", "observe"]


@dataclass(frozen=True, slots=True)
class PlanSeedResult:
    path: Path
    plan_id: str
    rows: int
    action: str
    dual_filtered: bool
    notes: tuple[str, ...] = ()


def seed_plan_from_prefix(
    *,
    db_path: Path,
    path_prefix: str,
    out: Path,
    action: SeedAction = "observe",
    limit: int = 500,
    dual_presence_only: bool = False,
    store_root: Path | None = None,
    mount_root: Path | None = None,
    register: bool = False,
    policy_name: str = "surface-plan-seed",
) -> PlanSeedResult:
    """Export claims under ``path_prefix`` to a plan TSV skeleton.

    * ``action=observe`` — writes ``reclassify`` rows with rationale seed only
      (no FS mutation on apply even if someone mis-runs execute later without
      changing actions — still operator must not execute unreviewed seeds).
    * ``action=retire_direct`` — only when ``dual_presence_only`` or operator
      explicitly opts in; dual-filtered path is the safe default for cloud.
    """
    prefix = (path_prefix or "").rstrip("/")
    if not prefix:
        raise ValueError("path_prefix is required for plan seed (refuse whole inventory)")
    if limit < 1 or limit > 50_000:
        raise ValueError("limit must be 1..50000")

    notes: list[str] = []
    con = connect(db_path, read_only=True, load_vec=False)
    try:
        rows_raw = con.execute(
            """
            SELECT c.permanode_id, p.canonical_hash, c.size_bytes, c.file_path, c.tier
            FROM claims c
            LEFT JOIN permanodes p ON p.id = c.permanode_id
            WHERE c.is_current = 1
              AND (c.file_path = ? OR c.file_path LIKE ?)
            ORDER BY c.size_bytes DESC, c.file_path ASC
            LIMIT ?
            """,
            (prefix, prefix + "/%", limit + 1),
        ).fetchall()
    finally:
        con.close()

    truncated = len(rows_raw) > limit
    if truncated:
        rows_raw = rows_raw[:limit]
        notes.append(f"truncated to {limit} claims (raise --limit or narrow --prefix)")

    selected: list[tuple[Any, ...]] = list(rows_raw)
    dual_filtered = False
    if dual_presence_only or action == "retire_direct":
        from steward.core.dual_presence import map_claim_to_pair
        from steward.infra.dual_presence import (
            default_mount_root,
            default_store_root,
            probe_pair,
        )

        sroot = str(store_root or default_store_root())
        mroot = str(mount_root or default_mount_root())
        kept: list[tuple[Any, ...]] = []
        for r in selected:
            path = str(r[3])
            pair = map_claim_to_pair(path, store_root=sroot, mount_root=mroot)
            probe = probe_pair(pair.store_path, pair.mount_path, relative=pair.relative)
            if dual_presence_only and probe.kind != "dual":
                continue
            kept.append(r)
        dual_filtered = True
        notes.append(
            f"dual-presence filter: {len(kept)}/{len(selected)} dual under "
            f"store={sroot}"
        )
        selected = kept

    plan_id = str(uuid.uuid4())
    # reclassify is inventory-metadata only; retire_direct is the cloud-safe action.
    row_action = "retire_direct" if action == "retire_direct" else "reclassify"
    m_rows: list[ManifestRow] = []
    for r in selected:
        pid, chash, size, path, tier = (
            str(r[0]),
            str(r[1] or ""),
            int(r[2] or 0),
            str(r[3]),
            str(r[4] or "unknown"),
        )
        # permanode_id is 32 hex; pad/truncate only if corrupt (tests use real scans).
        if len(pid) != 32:
            notes.append(f"skip non-32 permanode_id at {path}")
            continue
        m_rows.append(
            ManifestRow(
                action=row_action,  # type: ignore[arg-type]
                permanode_id=pid,
                canonical_hash=chash or ("0" * 64),
                size_bytes=size,
                source_path=path,
                source_tier=tier,
                destination_path=None,
                destination_tier=None,
                rationale=f"surface-plan-seed prefix={prefix} action={action}",
            )
        )

    header = ManifestHeader(
        produced_by_steward_version=__version__,
        produced_at=datetime.now(timezone.utc),
        policy_name=policy_name,
        phase_name="surface-plan-seed",
        manifest_run_id=plan_id,
    )
    manifest = Manifest(header=header, rows=tuple(m_rows))
    out = out.expanduser()
    write_manifest(out, manifest)
    notes.append("dry seed only — apply requires --dry-run / --execute (ADR-0002)")
    notes.append("no execute from surface; review TSV before any apply")

    if register and m_rows:
        from steward.infra.plans import register_plan_from_manifest

        register_plan_from_manifest(
            out,
            policy_name=policy_name,
            policy_kind="surface-seed",
            root_prefix=prefix,
            phase_name="surface-plan-seed",
            notes=tuple(notes),
            cloud_retire_ready=bool(action == "retire_direct" and dual_filtered),
        )
        notes.append(f"registered plan_id={plan_id}")

    return PlanSeedResult(
        path=out,
        plan_id=plan_id,
        rows=len(m_rows),
        action=row_action,
        dual_filtered=dual_filtered,
        notes=tuple(notes),
    )


__all__ = ["PlanSeedResult", "seed_plan_from_prefix"]
