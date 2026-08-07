# SPDX-License-Identifier: Apache-2.0

"""Plan backlog pure types and blocked-reason evaluation (ADR-0019).

Portable open-core surface: no SQLite / FS / launchctl. Registry I/O lives
in :mod:`steward.infra.plans`.
"""

from __future__ import annotations

from steward.core.plans.blocked import (
    BLOCKED_DRY_RUN_ERRORS,
    BLOCKED_DUAL_PRESENCE_UNFILTERED,
    BLOCKED_EMPTY_PLAN,
    BLOCKED_FP_NOT_READY,
    BLOCKED_MANIFEST_MISSING,
    BLOCKED_OVERSIZE_FOR_MCP,
    BLOCKED_STALE_INVENTORY,
    HARD_BLOCKED_REASONS,
    KNOWN_BLOCKED_REASONS,
    evaluate_plan_blocked_reasons,
)
from steward.core.plans.model import (
    DryRunDigest,
    PlanBacklogRecord,
    PlanFilters,
    PlanPolicyRef,
    PlanStatus,
    plan_record_to_compact_dict,
    plan_record_to_dict,
)

__all__ = [
    "BLOCKED_DRY_RUN_ERRORS",
    "BLOCKED_DUAL_PRESENCE_UNFILTERED",
    "BLOCKED_EMPTY_PLAN",
    "BLOCKED_FP_NOT_READY",
    "BLOCKED_MANIFEST_MISSING",
    "BLOCKED_OVERSIZE_FOR_MCP",
    "BLOCKED_STALE_INVENTORY",
    "HARD_BLOCKED_REASONS",
    "KNOWN_BLOCKED_REASONS",
    "DryRunDigest",
    "PlanBacklogRecord",
    "PlanFilters",
    "PlanPolicyRef",
    "PlanStatus",
    "evaluate_plan_blocked_reasons",
    "plan_record_to_compact_dict",
    "plan_record_to_dict",
]
