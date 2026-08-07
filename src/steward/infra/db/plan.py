# SPDX-License-Identifier: Apache-2.0

"""Plan-generation facade — opens DB, loads policy, runs reconciler, writes manifest.

ADR-0019: auto-registers plans into the data-dir backlog unless
``register=False`` (CLI ``--no-register``).
"""

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
from steward.infra.observability.swallowed import log_swallowed_error

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
    # ADR-0019 backlog fields
    plan_id: str | None = None
    estimated_bytes: int = 0
    blocked_reasons: tuple[str, ...] = ()
    registered_path: str | None = None
    action_counts: dict[str, int] | None = None


def plan_dedup_retire(
    *,
    policy_path: Path,
    out_path: Path,
    root_prefix: str | None = None,
    register: bool = True,
) -> PlanSummary:
    """Load ``policy_path`` as a RetentionPolicy, walk current claims,
    write a stash manifest to ``out_path``.

    Raises :class:`steward.core.errors.PolicyError` on bad policy YAML;
    raises :class:`FileNotFoundError` if the policy file is missing.
    """
    policy = load_policy(policy_path)
    if not isinstance(policy, RetentionPolicy):
        raise TypeError(f"plan_dedup_retire requires a RetentionPolicy YAML; got {type(policy).__name__}")

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
    estimated_bytes = sum(int(r.size_bytes) for r in manifest.rows)
    action_counts = _action_counts(manifest.rows)

    summary = PlanSummary(
        policy_path=policy_path,
        out_path=out_path,
        rows=len(manifest.rows),
        stash_rows=stash,
        nas_manifest_rows=nas,
        promote_rows=0,
        manifest_run_id=manifest.header.manifest_run_id,
        retire_direct_rows=retire,
        plan_id=manifest.header.manifest_run_id,
        estimated_bytes=estimated_bytes,
        action_counts=action_counts,
    )
    if register:
        summary = _maybe_register(
            summary,
            policy_path=policy_path,
            policy_kind="RetentionPolicy",
            root_prefix=root_prefix,
        )
    return summary


def plan_promote(
    *,
    policy_path: Path,
    out_path: Path,
    phase_name: str | None = None,
    max_files: int | None = None,
    register: bool = True,
) -> PlanSummary:
    """Load ``policy_path`` as a PromotionPolicy, walk Backup-only permanodes,
    write a promote manifest to ``out_path``.
    """
    policy = load_policy(policy_path)
    if not isinstance(policy, PromotionPolicy):
        raise TypeError(f"plan_promote requires a PromotionPolicy YAML; got {type(policy).__name__}")

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
    estimated_bytes = sum(int(r.size_bytes) for r in manifest.rows)
    action_counts = _action_counts(manifest.rows)

    summary = PlanSummary(
        policy_path=policy_path,
        out_path=out_path,
        rows=len(manifest.rows),
        stash_rows=0,
        nas_manifest_rows=0,
        promote_rows=promote,
        manifest_run_id=manifest.header.manifest_run_id,
        retire_direct_rows=0,
        plan_id=manifest.header.manifest_run_id,
        estimated_bytes=estimated_bytes,
        action_counts=action_counts,
    )
    if register:
        summary = _maybe_register(
            summary,
            policy_path=policy_path,
            policy_kind="PromotionPolicy",
            phase_name=phase_name,
            max_files=max_files,
        )
    return summary


def plan(
    *,
    policy_path: Path,
    out_path: Path,
    root_prefix: str | None = None,
    phase_name: str | None = None,
    max_files: int | None = None,
    register: bool = True,
) -> PlanSummary:
    """Dispatch by policy kind. ``root_prefix`` applies only to RetentionPolicy;
    ``phase_name`` + ``max_files`` apply only to PromotionPolicy."""
    policy = load_policy(policy_path)
    if isinstance(policy, RetentionPolicy):
        return plan_dedup_retire(
            policy_path=policy_path,
            out_path=out_path,
            root_prefix=root_prefix,
            register=register,
        )
    if isinstance(policy, PromotionPolicy):
        return plan_promote(
            policy_path=policy_path,
            out_path=out_path,
            phase_name=phase_name,
            max_files=max_files,
            register=register,
        )
    raise TypeError(f"plan: no reconciler for {type(policy).__name__}")


def _action_counts(rows: object) -> dict[str, int]:
    from collections import Counter

    return dict(Counter(r.action for r in rows))  # type: ignore[attr-defined]


def _maybe_register(
    summary: PlanSummary,
    *,
    policy_path: Path,
    policy_kind: str,
    root_prefix: str | None = None,
    phase_name: str | None = None,
    max_files: int | None = None,
) -> PlanSummary:
    """Best-effort backlog registration; never fail plan generation."""
    try:
        from steward.infra.mcp.capability import mcp_max_files_cap
        from steward.infra.plans.registry import register_plan_from_manifest

        cloud_ready: bool | None = None
        try:
            from steward.infra.fp_status import collect_fp_status

            if (summary.retire_direct_rows or 0) > 0:
                fp = collect_fp_status()
                cloud_ready = bool(getattr(fp.verdict, "cloud_retire_ready", False))
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("db.plan.fp_probe_register", exc, context={})

        try:
            cap: int | None = mcp_max_files_cap()
        except Exception:  # noqa: BLE001
            cap = None

        rec = register_plan_from_manifest(
            summary.out_path,
            policy_name=policy_path.name,
            policy_path=str(policy_path),
            policy_kind=policy_kind,
            root_prefix=root_prefix,
            phase_name=phase_name,
            max_files=max_files,
            cloud_retire_ready=cloud_ready,
            mcp_max_files_cap=cap,
        )
        return PlanSummary(
            policy_path=summary.policy_path,
            out_path=summary.out_path,
            rows=summary.rows,
            stash_rows=summary.stash_rows,
            nas_manifest_rows=summary.nas_manifest_rows,
            promote_rows=summary.promote_rows,
            manifest_run_id=summary.manifest_run_id,
            retire_direct_rows=summary.retire_direct_rows,
            plan_id=rec.plan_id,
            estimated_bytes=rec.estimated_bytes,
            blocked_reasons=rec.blocked_reasons,
            registered_path=rec.manifest_path,
            action_counts=dict(rec.action_counts),
        )
    except Exception as exc:  # noqa: BLE001 — registration is best-effort
        log_swallowed_error(
            "db.plan.register",
            exc,
            context={"out_path": str(summary.out_path)},
        )
        return summary
