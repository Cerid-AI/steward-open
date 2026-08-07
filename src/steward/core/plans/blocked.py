# SPDX-License-Identifier: Apache-2.0

"""Pure blocked-reason evaluation for plan backlog (ADR-0019 §2.1).

Blocked reasons are **advisory labels** for queues and agents. They do
not replace apply preflight or ADR-0002 execute gates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

BLOCKED_EMPTY_PLAN = "empty_plan"
BLOCKED_FP_NOT_READY = "fp_not_ready"
BLOCKED_DUAL_PRESENCE_UNFILTERED = "dual_presence_unfiltered"
BLOCKED_OVERSIZE_FOR_MCP = "oversize_for_mcp"
BLOCKED_DRY_RUN_ERRORS = "dry_run_errors"
BLOCKED_MANIFEST_MISSING = "manifest_missing"
BLOCKED_STALE_INVENTORY = "stale_inventory"

KNOWN_BLOCKED_REASONS: frozenset[str] = frozenset(
    {
        BLOCKED_EMPTY_PLAN,
        BLOCKED_FP_NOT_READY,
        BLOCKED_DUAL_PRESENCE_UNFILTERED,
        BLOCKED_OVERSIZE_FOR_MCP,
        BLOCKED_DRY_RUN_ERRORS,
        BLOCKED_MANIFEST_MISSING,
        BLOCKED_STALE_INVENTORY,
    }
)

# Reasons that set status=blocked (operator can still CLI-apply).
HARD_BLOCKED_REASONS: frozenset[str] = frozenset(
    {
        BLOCKED_EMPTY_PLAN,
        BLOCKED_MANIFEST_MISSING,
        BLOCKED_DRY_RUN_ERRORS,
    }
)

# Actions that typically need cloud-FP readiness for safe bulk execute.
_FP_GATED_ACTIONS: frozenset[str] = frozenset({"retire_direct"})

# Heuristic: large retire plans on Dropbox paths without dual-presence filter.
_DUAL_PRESENCE_MIN_RETIRE = 1000
_DROPBOX_PATH_MARKERS: tuple[str, ...] = (
    "dropbox",
    "cloudstorage",
    "fileprovider",
)


def evaluate_plan_blocked_reasons(
    *,
    rows_total: int,
    action_counts: Mapping[str, int] | None = None,
    estimated_bytes: int = 0,
    manifest_exists: bool = True,
    dry_run_errors: int | None = None,
    cloud_retire_ready: bool | None = None,
    has_dual_presence_filter: bool = False,
    sample_source_paths: Sequence[str] | None = None,
    mcp_max_files_cap: int | None = None,
    inventory_stale: bool = False,
) -> tuple[str, ...]:
    """Return stable blocked-reason tokens for a plan (order fixed).

    Parameters are intentionally pure inputs — callers probe FS/FP/MCP
    and pass boolean/int flags. No I/O here.
    """
    counts = dict(action_counts or {})
    reasons: list[str] = []

    if rows_total <= 0:
        reasons.append(BLOCKED_EMPTY_PLAN)

    if not manifest_exists:
        reasons.append(BLOCKED_MANIFEST_MISSING)

    if dry_run_errors is not None and dry_run_errors > 0:
        reasons.append(BLOCKED_DRY_RUN_ERRORS)

    retire_n = int(counts.get("retire_direct", 0))
    if retire_n > 0 and cloud_retire_ready is False:
        reasons.append(BLOCKED_FP_NOT_READY)

    if (
        retire_n >= _DUAL_PRESENCE_MIN_RETIRE
        and not has_dual_presence_filter
        and _looks_like_dropbox_plan(sample_source_paths, counts)
    ):
        reasons.append(BLOCKED_DUAL_PRESENCE_UNFILTERED)

    if mcp_max_files_cap is not None and rows_total > int(mcp_max_files_cap):
        reasons.append(BLOCKED_OVERSIZE_FOR_MCP)

    if inventory_stale:
        reasons.append(BLOCKED_STALE_INVENTORY)

    # Stable order matches KNOWN set definition order for tests/UX.
    order = (
        BLOCKED_EMPTY_PLAN,
        BLOCKED_MANIFEST_MISSING,
        BLOCKED_DRY_RUN_ERRORS,
        BLOCKED_FP_NOT_READY,
        BLOCKED_DUAL_PRESENCE_UNFILTERED,
        BLOCKED_OVERSIZE_FOR_MCP,
        BLOCKED_STALE_INVENTORY,
    )
    return tuple(r for r in order if r in reasons)


def _looks_like_dropbox_plan(
    sample_paths: Sequence[str] | None,
    counts: Mapping[str, int],
) -> bool:
    """Heuristic: any sample path contains Dropbox/FP markers, or only retire_direct."""
    if sample_paths:
        for p in sample_paths:
            low = str(p).lower()
            if any(m in low for m in _DROPBOX_PATH_MARKERS):
                return True
        return False
    # No samples: still flag large pure retire_direct plans (field 221k class).
    total = sum(int(v) for v in counts.values()) or 0
    retire = int(counts.get("retire_direct", 0))
    return total > 0 and retire == total


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
    "evaluate_plan_blocked_reasons",
]
