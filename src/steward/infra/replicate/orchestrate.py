# SPDX-License-Identifier: Apache-2.0

"""Orchestration facade for ``steward replicate``.

Opens the inventory DB connection, loads + validates the policy, runs
:func:`run_replication`, commits, returns the report. Mirrors the
pattern in :mod:`steward.infra.scanner.orchestrate` so the CLI doesn't
need to know about ``infra.db.connect`` directly.
"""
from __future__ import annotations

from pathlib import Path

from steward.core.policy.loader import load_policy
from steward.core.policy.schema import ReplicationPolicy
from steward.infra.db.connect import connect
from steward.infra.replicate.runner import ReplicationReport, run_replication


def _bundled_policies_dir() -> Path:
    """Path to the ``src/steward/policies`` directory bundled with the wheel."""
    return Path(__file__).resolve().parents[2] / "policies"


def resolve_policy_path(name_or_path: str) -> Path:
    """Resolve ``name_or_path`` to a policy file.

    Accepts either an absolute/relative filesystem path or a bare
    filename inside the bundled-policies dir (e.g. ``replication.yml``).
    """
    p = Path(name_or_path)
    if p.exists():
        return p
    bundled = _bundled_policies_dir() / name_or_path
    if bundled.exists():
        return bundled
    raise FileNotFoundError(f"replication policy not found: {name_or_path}")


def run_replicate(
    *,
    db_path: Path,
    policy_path: Path,
    machine_id: str,
    dry_run: bool,
) -> ReplicationReport:
    """Open ``db_path``, load the policy, run replication, commit."""
    loaded = load_policy(policy_path)
    if not isinstance(loaded, ReplicationPolicy):
        raise ValueError(
            f"policy at {policy_path} is not a ReplicationPolicy "
            f"(got {type(loaded).__name__})"
        )

    con = connect(db_path)
    try:
        report = run_replication(
            con=con,
            policy=loaded,
            machine_id=machine_id,
            dry_run=dry_run,
            policy_name=policy_path.name,
        )
        con.commit()
    finally:
        con.close()
    return report


__all__ = ["resolve_policy_path", "run_replicate"]
