# SPDX-License-Identifier: Apache-2.0

"""Orchestration facade for ``steward archive``.

Opens the inventory DB, loads + validates the policy, runs the chosen
operation, commits, returns the report. Mirrors the pattern in
:mod:`steward.infra.replicate.orchestrate`.
"""
from __future__ import annotations

from pathlib import Path

from steward.core.policy.loader import load_policy
from steward.core.policy.schema import ArchivePolicy
from steward.infra.archive.restic import ResticRunResult
from steward.infra.archive.runner import (
    ArchiveListReport,
    ArchiveSnapshotReport,
    run_archive_init,
    run_archive_list,
    run_archive_snapshot,
)
from steward.infra.db.connect import connect


def _bundled_policies_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "policies"


def resolve_policy_path(name_or_path: str) -> Path:
    """Resolve ``name_or_path`` to a policy file (bundled or filesystem)."""
    p = Path(name_or_path)
    if p.exists():
        return p
    bundled = _bundled_policies_dir() / name_or_path
    if bundled.exists():
        return bundled
    raise FileNotFoundError(f"archive policy not found: {name_or_path}")


def _load_archive_policy(policy_path: Path) -> ArchivePolicy:
    loaded = load_policy(policy_path)
    if not isinstance(loaded, ArchivePolicy):
        raise ValueError(
            f"policy at {policy_path} is not an ArchivePolicy "
            f"(got {type(loaded).__name__})"
        )
    return loaded


def run_snapshot(
    *,
    db_path: Path,
    policy_path: Path,
    machine_id: str,
    dry_run: bool,
) -> ArchiveSnapshotReport:
    """Open ``db_path``, run a snapshot pass, commit."""
    policy = _load_archive_policy(policy_path)
    con = connect(db_path)
    try:
        report = run_archive_snapshot(
            con=con,
            policy=policy,
            machine_id=machine_id,
            dry_run=dry_run,
            policy_name=policy_path.name,
        )
        con.commit()
    finally:
        con.close()
    return report


def run_list(
    *,
    db_path: Path,
    policy_path: Path,
    machine_id: str,
) -> ArchiveListReport:
    """Open ``db_path``, run a snapshots-list pass, commit."""
    policy = _load_archive_policy(policy_path)
    con = connect(db_path)
    try:
        report = run_archive_list(
            con=con,
            policy=policy,
            machine_id=machine_id,
            policy_name=policy_path.name,
        )
        con.commit()
    finally:
        con.close()
    return report


def run_init(
    *,
    db_path: Path,
    policy_path: Path,
    machine_id: str,
) -> list[tuple[str, ResticRunResult]]:
    """Open ``db_path``, init each unique repository, commit."""
    policy = _load_archive_policy(policy_path)
    con = connect(db_path)
    try:
        results = run_archive_init(
            con=con,
            policy=policy,
            machine_id=machine_id,
            policy_name=policy_path.name,
        )
        con.commit()
    finally:
        con.close()
    return results


__all__ = ["resolve_policy_path", "run_init", "run_list", "run_snapshot"]
