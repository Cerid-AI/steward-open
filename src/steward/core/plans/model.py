# SPDX-License-Identifier: Apache-2.0

"""I/O-free plan backlog record types (ADR-0019)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

PlanStatus = Literal[
    "registered",
    "blocked",
    "dry_run_ok",
    "dry_run_failed",
    "applied",
    "partially_applied",
    "superseded",
    "expired",
]


@dataclass(frozen=True, slots=True)
class PlanPolicyRef:
    """Resolved policy identity for a backlog plan."""

    name: str
    path: str
    kind: str


@dataclass(frozen=True, slots=True)
class PlanFilters:
    """Optional generation filters attached at plan time."""

    root_prefix: str | None = None
    phase_name: str | None = None
    max_files: int | None = None


@dataclass(frozen=True, slots=True)
class DryRunDigest:
    """Compact latest apply --dry-run summary (not full apply report)."""

    ok: bool
    errors: int = 0
    applied: int = 0
    skipped: int = 0
    at: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class PlanBacklogRecord:
    """First-class plan backlog object (data-dir registry; not inventory.db)."""

    plan_id: str
    created_at: str
    machine_id: str
    policy: PlanPolicyRef
    filters: PlanFilters
    action_counts: dict[str, int]
    rows_total: int
    estimated_bytes: int
    blocked_reasons: tuple[str, ...]
    status: PlanStatus
    manifest_path: str
    manifest_sha256: str | None = None
    dry_run: DryRunDigest | None = None
    notes: tuple[str, ...] = ()
    parent_plan_id: str | None = None
    filter_stats_path: str | None = None


def plan_record_to_dict(record: PlanBacklogRecord) -> dict[str, Any]:
    """JSON-stable full serialization for show / summary.json."""
    d = asdict(record)
    d["blocked_reasons"] = list(record.blocked_reasons)
    d["notes"] = list(record.notes)
    d["action_counts"] = dict(sorted(record.action_counts.items()))
    return d


def plan_record_to_compact_dict(record: PlanBacklogRecord) -> dict[str, Any]:
    """Compact line for index.jsonl / list views."""
    return {
        "plan_id": record.plan_id,
        "created_at": record.created_at,
        "machine_id": record.machine_id,
        "policy": record.policy.name,
        "policy_kind": record.policy.kind,
        "rows_total": record.rows_total,
        "estimated_bytes": record.estimated_bytes,
        "action_counts": dict(sorted(record.action_counts.items())),
        "blocked_reasons": list(record.blocked_reasons),
        "status": record.status,
        "manifest_path": record.manifest_path,
    }


__all__ = [
    "DryRunDigest",
    "PlanBacklogRecord",
    "PlanFilters",
    "PlanPolicyRef",
    "PlanStatus",
    "plan_record_to_compact_dict",
    "plan_record_to_dict",
]
