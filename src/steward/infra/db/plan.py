# SPDX-License-Identifier: Apache-2.0

"""Plan-generation facade — opens DB, loads policy, runs reconciler, writes manifest."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from steward._version import __version__
from steward.core.manifest_io import write_manifest
from steward.core.policy import load_policy
from steward.core.policy.reconciler import (
    load_current_claims_from_db,
    reconcile_dedup_retire,
    reconcile_promote,
)
from steward.core.policy.schema import PromotionPolicy, RetentionPolicy
from steward.infra.db.connect import connect
from steward.infra.db.settings import inventory_db_path

logger = logging.getLogger("steward.infra.db.plan")


@dataclass(frozen=True)
class PlanSummary:
    policy_path: Path
    out_path: Path
    rows: int
    stash_rows: int
    nas_manifest_rows: int
    promote_rows: int
    manifest_run_id: str
    retire_direct_rows: int = 0


def plan_dedup_retire(
    *,
    policy_path: Path,
    out_path: Path,
    root_prefix: str | None = None,
) -> PlanSummary:
    """Load ``policy_path`` as a RetentionPolicy, walk current claims,
    write a stash manifest to ``out_path``.

    Raises :class:`steward.core.errors.PolicyError` on bad policy YAML;
    raises :class:`FileNotFoundError` if the policy file is missing.
    """
    policy = load_policy(policy_path)
    if not isinstance(policy, RetentionPolicy):
        raise TypeError(
            f"plan_dedup_retire requires a RetentionPolicy YAML; got {type(policy).__name__}"
        )

    target = inventory_db_path()
    con = connect(target, read_only=True, load_vec=False)
    try:
        claims = load_current_claims_from_db(con)
    finally:
        con.close()

    manifest = reconcile_dedup_retire(
        claims=claims,
        policy=policy,
        steward_version=__version__,
        root_prefix=root_prefix,
    )
    write_manifest(out_path, manifest)

    stash = sum(1 for r in manifest.rows if r.action == "stash")
    nas = sum(1 for r in manifest.rows if r.action == "nas_manifest")
    retire = sum(1 for r in manifest.rows if r.action == "retire_direct")
    return PlanSummary(
        policy_path=policy_path,
        out_path=out_path,
        rows=len(manifest.rows),
        stash_rows=stash,
        nas_manifest_rows=nas,
        promote_rows=0,
        manifest_run_id=manifest.header.manifest_run_id,
        retire_direct_rows=retire,
    )


def plan_promote(
    *,
    policy_path: Path,
    out_path: Path,
    phase_name: str | None = None,
    max_files: int | None = None,
) -> PlanSummary:
    """Load ``policy_path`` as a PromotionPolicy, walk Backup-only permanodes,
    write a promote manifest to ``out_path``.
    """
    policy = load_policy(policy_path)
    if not isinstance(policy, PromotionPolicy):
        raise TypeError(
            f"plan_promote requires a PromotionPolicy YAML; got {type(policy).__name__}"
        )

    target = inventory_db_path()
    con = connect(target, read_only=True, load_vec=False)
    try:
        claims = load_current_claims_from_db(con)
    finally:
        con.close()

    manifest = reconcile_promote(
        claims=claims,
        policy=policy,
        steward_version=__version__,
        phase_name=phase_name,
        max_files=max_files,
    )
    write_manifest(out_path, manifest)

    promote = sum(1 for r in manifest.rows if r.action == "promote")
    return PlanSummary(
        policy_path=policy_path,
        out_path=out_path,
        rows=len(manifest.rows),
        stash_rows=0,
        nas_manifest_rows=0,
        promote_rows=promote,
        manifest_run_id=manifest.header.manifest_run_id,
        retire_direct_rows=0,
    )


def plan(
    *,
    policy_path: Path,
    out_path: Path,
    root_prefix: str | None = None,
    phase_name: str | None = None,
    max_files: int | None = None,
) -> PlanSummary:
    """Dispatch by policy kind. ``root_prefix`` applies only to RetentionPolicy;
    ``phase_name`` + ``max_files`` apply only to PromotionPolicy."""
    policy = load_policy(policy_path)
    if isinstance(policy, RetentionPolicy):
        return plan_dedup_retire(
            policy_path=policy_path,
            out_path=out_path,
            root_prefix=root_prefix,
        )
    if isinstance(policy, PromotionPolicy):
        return plan_promote(
            policy_path=policy_path,
            out_path=out_path,
            phase_name=phase_name,
            max_files=max_files,
        )
    raise TypeError(f"plan: no reconciler for {type(policy).__name__}")
