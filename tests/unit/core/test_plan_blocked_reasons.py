# SPDX-License-Identifier: Apache-2.0

"""Unit tests for pure plan blocked-reason evaluation (ADR-0019)."""

from __future__ import annotations

from steward.core.plans import (
    BLOCKED_DRY_RUN_ERRORS,
    BLOCKED_DUAL_PRESENCE_UNFILTERED,
    BLOCKED_EMPTY_PLAN,
    BLOCKED_FP_NOT_READY,
    BLOCKED_MANIFEST_MISSING,
    BLOCKED_OVERSIZE_FOR_MCP,
    BLOCKED_STALE_INVENTORY,
    evaluate_plan_blocked_reasons,
)


def test_empty_plan() -> None:
    assert evaluate_plan_blocked_reasons(rows_total=0) == (BLOCKED_EMPTY_PLAN,)


def test_clean_small_plan() -> None:
    reasons = evaluate_plan_blocked_reasons(
        rows_total=10,
        action_counts={"stash": 10},
        manifest_exists=True,
    )
    assert reasons == ()


def test_manifest_missing() -> None:
    r = evaluate_plan_blocked_reasons(rows_total=5, manifest_exists=False)
    assert BLOCKED_MANIFEST_MISSING in r


def test_dry_run_errors() -> None:
    r = evaluate_plan_blocked_reasons(rows_total=5, dry_run_errors=2)
    assert BLOCKED_DRY_RUN_ERRORS in r


def test_fp_not_ready_only_with_retire() -> None:
    r = evaluate_plan_blocked_reasons(
        rows_total=10,
        action_counts={"retire_direct": 10},
        cloud_retire_ready=False,
    )
    assert BLOCKED_FP_NOT_READY in r
    r2 = evaluate_plan_blocked_reasons(
        rows_total=10,
        action_counts={"stash": 10},
        cloud_retire_ready=False,
    )
    assert BLOCKED_FP_NOT_READY not in r2


def test_dual_presence_unfiltered_large_retire_dropbox() -> None:
    r = evaluate_plan_blocked_reasons(
        rows_total=5000,
        action_counts={"retire_direct": 5000},
        sample_source_paths=["/Users/x/Library/CloudStorage/Dropbox/a.txt"],
        has_dual_presence_filter=False,
    )
    assert BLOCKED_DUAL_PRESENCE_UNFILTERED in r
    r2 = evaluate_plan_blocked_reasons(
        rows_total=5000,
        action_counts={"retire_direct": 5000},
        sample_source_paths=["/Users/x/Library/CloudStorage/Dropbox/a.txt"],
        has_dual_presence_filter=True,
    )
    assert BLOCKED_DUAL_PRESENCE_UNFILTERED not in r2


def test_oversize_for_mcp() -> None:
    r = evaluate_plan_blocked_reasons(rows_total=100, mcp_max_files_cap=50)
    assert BLOCKED_OVERSIZE_FOR_MCP in r
    r2 = evaluate_plan_blocked_reasons(rows_total=10, mcp_max_files_cap=50)
    assert BLOCKED_OVERSIZE_FOR_MCP not in r2


def test_stale_inventory() -> None:
    r = evaluate_plan_blocked_reasons(rows_total=1, inventory_stale=True)
    assert BLOCKED_STALE_INVENTORY in r


def test_combination_stable_order() -> None:
    r = evaluate_plan_blocked_reasons(
        rows_total=0,
        manifest_exists=False,
        dry_run_errors=1,
        inventory_stale=True,
    )
    assert r[0] == BLOCKED_EMPTY_PLAN
    assert BLOCKED_MANIFEST_MISSING in r
    assert BLOCKED_DRY_RUN_ERRORS in r
    assert BLOCKED_STALE_INVENTORY in r
