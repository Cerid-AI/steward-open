# SPDX-License-Identifier: Apache-2.0

"""Pure estate-health scoring and ``--fail-on`` evaluation (ADR-0017).

No SQLite, filesystem, or network. Unit-testable without infra.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

from steward.core.health.model import (
    AdapterFreshness,
    AttachedImportHealth,
    DualPresenceSection,
    EstateHealthReport,
    FPSection,
    HealthCheckResult,
    HealthLevel,
    HealthRollupInfo,
    InventoryIntegrity,
    MountProbe,
    RootScanFreshness,
    StashHealth,
)
from steward.core.health.thresholds import (
    DEFAULT_THRESHOLDS,
    FAIL_ON_BROKEN_AUDIT,
    FAIL_ON_DUAL_PRESENCE_POOR,
    FAIL_ON_FP_NOT_READY,
    FAIL_ON_ROLLUP_STALE,
    FAIL_ON_STALE_SCAN,
    FAIL_ON_STASH_OVERDUE,
    KNOWN_FAIL_ON_TOKENS,
    HealthThresholds,
)

_LEVEL_RANK: dict[HealthLevel, int] = {
    "ok": 0,
    "skipped": 0,
    "unknown": 1,
    "warn": 2,
    "fail": 3,
}


def parse_iso_to_utc(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp to aware UTC, or None on failure."""
    if not value:
        return None
    try:
        ts = str(value).replace("Z", "+00:00")
        when = datetime.fromisoformat(ts)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def age_hours(
    iso_ts: str | None,
    *,
    now: datetime | None = None,
) -> float | None:
    """Hours between ``iso_ts`` and ``now`` (UTC). None if unparseable."""
    when = parse_iso_to_utc(iso_ts)
    if when is None:
        return None
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return max(0.0, (ref - when).total_seconds() / 3600.0)


def level_for_age(
    age: float | None,
    max_age: float,
    *,
    missing_level: HealthLevel = "fail",
) -> HealthLevel:
    """Map age vs max_age: None → missing_level; over → fail; else ok."""
    if age is None:
        return missing_level
    if age > max_age:
        return "fail"
    return "ok"


def worst_level(levels: Iterable[HealthLevel]) -> HealthLevel:
    """Return the most severe level among ``levels`` (empty → unknown)."""
    best: HealthLevel = "unknown"
    rank = -1
    any_level = False
    for level in levels:
        any_level = True
        r = _LEVEL_RANK.get(level, 1)
        if r > rank:
            rank = r
            best = level
    return best if any_level else "unknown"


def free_space_level(
    free_bytes: int | None,
    total_bytes: int | None,
    *,
    thresholds: HealthThresholds,
) -> HealthLevel:
    """Warn when free space is below absolute or ratio floors."""
    if free_bytes is None:
        return "unknown"
    if free_bytes < thresholds.free_bytes_min:
        return "warn"
    if total_bytes is not None and total_bytes > 0:
        ratio = free_bytes / float(total_bytes)
        if ratio < thresholds.free_ratio_min:
            return "warn"
    return "ok"


def latency_level(
    latency_ms: float | None,
    *,
    thresholds: HealthThresholds,
) -> HealthLevel:
    if latency_ms is None:
        return "unknown"
    if latency_ms > thresholds.sample_latency_warn_ms:
        return "warn"
    return "ok"


# ─────────────────────── named checks ──────────────────────────


def check_stale_scan(
    roots: Sequence[RootScanFreshness],
    *,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
) -> HealthCheckResult:
    """Fail when any tracked root is stale or has no finished scan."""
    if not roots:
        return HealthCheckResult(
            name=FAIL_ON_STALE_SCAN,
            level="fail",
            message="No finished scan_runs for any root",
            details={"roots": 0, "max_age_hours": thresholds.scan_max_age_hours},
        )
    stale: list[dict[str, Any]] = []
    unfinished: list[dict[str, Any]] = []
    for r in roots:
        if r.unfinished:
            unfinished.append(
                {
                    "root_path": r.root_path,
                    "started_at": r.unfinished_started_at,
                }
            )
        if r.level == "fail" or r.finished_at is None:
            stale.append(
                {
                    "root_path": r.root_path,
                    "finished_at": r.finished_at,
                    "age_hours": r.age_hours,
                    "tier": r.tier,
                }
            )
    if stale:
        return HealthCheckResult(
            name=FAIL_ON_STALE_SCAN,
            level="fail",
            message=f"{len(stale)} root(s) with stale or missing finished scan",
            details={
                "stale": stale,
                "max_age_hours": thresholds.scan_max_age_hours,
                "unfinished": unfinished,
            },
        )
    if unfinished:
        return HealthCheckResult(
            name=FAIL_ON_STALE_SCAN,
            level="warn",
            message=f"{len(unfinished)} unfinished scan(s) in progress",
            details={"unfinished": unfinished},
        )
    return HealthCheckResult(
        name=FAIL_ON_STALE_SCAN,
        level="ok",
        message="All tracked roots have fresh finished scans",
        details={"roots": len(roots), "max_age_hours": thresholds.scan_max_age_hours},
    )


def check_broken_audit(inventory: InventoryIntegrity) -> HealthCheckResult:
    """Fail when chain verified and not ok; skipped → skipped (cannot fail)."""
    if inventory.audit_skipped:
        return HealthCheckResult(
            name=FAIL_ON_BROKEN_AUDIT,
            level="skipped",
            message="Audit chain verification skipped (quick path)",
            details={"skipped": True},
        )
    if inventory.audit_ok is True:
        return HealthCheckResult(
            name=FAIL_ON_BROKEN_AUDIT,
            level="ok",
            message="Audit chain intact",
            details={
                "rows_checked": inventory.audit_rows_checked,
                "ok": True,
            },
        )
    if inventory.audit_ok is False:
        return HealthCheckResult(
            name=FAIL_ON_BROKEN_AUDIT,
            level="fail",
            message=inventory.audit_error or "Audit chain verification failed",
            details={
                "rows_checked": inventory.audit_rows_checked,
                "ok": False,
                "error": inventory.audit_error,
            },
        )
    return HealthCheckResult(
        name=FAIL_ON_BROKEN_AUDIT,
        level="unknown",
        message="Audit chain status unknown",
        details={},
    )


def check_stash_overdue(
    stash: StashHealth,
    *,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
) -> HealthCheckResult:
    """Fail when oldest in-flight stash exceeds cooling-off + grace."""
    if stash.source == "skipped":
        return HealthCheckResult(
            name=FAIL_ON_STASH_OVERDUE,
            level="skipped",
            message="Stash summary skipped (quick path without cache)",
            details={"source": stash.source},
        )
    limit = thresholds.stash_overdue_hours
    if stash.in_flight_entries == 0:
        return HealthCheckResult(
            name=FAIL_ON_STASH_OVERDUE,
            level="ok",
            message="No in-flight stash entries",
            details={"in_flight": 0, "limit_hours": limit},
        )
    if stash.overdue is True or (
        stash.age_hours_oldest is not None and stash.age_hours_oldest > limit
    ):
        return HealthCheckResult(
            name=FAIL_ON_STASH_OVERDUE,
            level="fail",
            message=(
                f"Oldest in-flight stash is {stash.age_hours_oldest:.1f}h "
                f"(limit {limit:.1f}h = cooling_off {thresholds.cooling_off_days}d "
                f"+ grace {thresholds.stash_grace_hours}h)"
                if stash.age_hours_oldest is not None
                else "In-flight stash overdue"
            ),
            details={
                "in_flight": stash.in_flight_entries,
                "oldest_ts": stash.oldest_ts_iso,
                "age_hours_oldest": stash.age_hours_oldest,
                "limit_hours": limit,
                "source": stash.source,
            },
        )
    if stash.age_hours_oldest is None:
        return HealthCheckResult(
            name=FAIL_ON_STASH_OVERDUE,
            level="unknown",
            message="Stash present but oldest timestamp unknown",
            details={"in_flight": stash.in_flight_entries, "source": stash.source},
        )
    return HealthCheckResult(
        name=FAIL_ON_STASH_OVERDUE,
        level="ok",
        message="In-flight stash within cooling-off + grace",
        details={
            "in_flight": stash.in_flight_entries,
            "age_hours_oldest": stash.age_hours_oldest,
            "limit_hours": limit,
        },
    )


def check_fp_not_ready(fp: FPSection) -> HealthCheckResult:
    """Fail when FP section present and cloud_retire_ready is false."""
    if not fp.present:
        return HealthCheckResult(
            name=FAIL_ON_FP_NOT_READY,
            level="skipped",
            message="FP section not collected",
            details={"present": False},
        )
    if fp.cloud_retire_ready is True and not fp.problems:
        return HealthCheckResult(
            name=FAIL_ON_FP_NOT_READY,
            level="ok",
            message="FP layout ready for cloud-propagating retire",
            details={
                "layout": fp.layout,
                "cloud_retire_ready": True,
            },
        )
    if fp.cloud_retire_ready is False or fp.problems:
        msg = "; ".join(fp.problems) if fp.problems else "cloud_retire_ready is false"
        return HealthCheckResult(
            name=FAIL_ON_FP_NOT_READY,
            level="fail",
            message=msg,
            details={
                "layout": fp.layout,
                "cloud_retire_ready": fp.cloud_retire_ready,
                "problems": list(fp.problems),
            },
        )
    return HealthCheckResult(
        name=FAIL_ON_FP_NOT_READY,
        level="unknown",
        message="FP readiness unknown",
        details={"layout": fp.layout},
    )


def check_rollup_stale(
    inventory: InventoryIntegrity,
    rollups: HealthRollupInfo | None,
    *,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
) -> HealthCheckResult:
    """Fail when rollup cache is missing beyond max age without live recount.

    Live recount (``counts_source=live``) is ok even without cache.
    """
    max_h = thresholds.rollup_max_age_hours
    if inventory.counts_source == "live":
        return HealthCheckResult(
            name=FAIL_ON_ROLLUP_STALE,
            level="ok",
            message="Inventory counts from live recount",
            details={"counts_source": "live", "max_age_hours": max_h},
        )
    if inventory.counts_source == "rollup" or (rollups is not None and rollups.used_cache):
        age = inventory.rollup_age_hours
        if rollups is not None and rollups.age_hours is not None:
            age = rollups.age_hours
        if age is not None and age > max_h:
            return HealthCheckResult(
                name=FAIL_ON_ROLLUP_STALE,
                level="fail",
                message=f"Rollup cache age {age:.1f}h exceeds {max_h:.1f}h",
                details={
                    "age_hours": age,
                    "max_age_hours": max_h,
                    "refreshed_at": inventory.rollup_refreshed_at
                    or (rollups.refreshed_at if rollups else None),
                },
            )
        return HealthCheckResult(
            name=FAIL_ON_ROLLUP_STALE,
            level="ok",
            message="Rollup cache within max age",
            details={
                "age_hours": age,
                "max_age_hours": max_h,
                "used_cache": True,
            },
        )
    # unknown / no cache and no live path recorded
    return HealthCheckResult(
        name=FAIL_ON_ROLLUP_STALE,
        level="unknown",
        message="Rollup cache missing; counts source unknown",
        details={
            "counts_source": inventory.counts_source,
            "max_age_hours": max_h,
        },
    )



def check_dual_presence_poor(
    dual: DualPresenceSection | None,
    *,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
) -> HealthCheckResult:
    """Fail when dual/(dual+store_only) is below threshold and mount side exists.

    Opt-in fail-on token (not in DEFAULT_CHECK_FAIL_ON). Skipped when section
    absent or not collected.
    """
    if dual is None or not dual.present:
        return HealthCheckResult(
            name=FAIL_ON_DUAL_PRESENCE_POOR,
            level="skipped",
            message="Dual-presence section not collected",
            details={"present": False},
        )
    if dual.level == "unknown" and dual.counted == 0:
        return HealthCheckResult(
            name=FAIL_ON_DUAL_PRESENCE_POOR,
            level="unknown",
            message="Dual-presence sample empty or skipped",
            details={"counted": dual.counted},
        )
    ratio = dual.cloud_safe_sample_ratio
    thr = thresholds.dual_presence_ratio_min
    denom = dual.dual + dual.store_only
    if denom <= 0:
        # No dual or store_only among probed — cannot score ratio
        if dual.mount_error > 0 or dual.missing_store == dual.counted:
            return HealthCheckResult(
                name=FAIL_ON_DUAL_PRESENCE_POOR,
                level="warn",
                message="No dual/store_only samples to score ratio",
                details={
                    "counted": dual.counted,
                    "dual": dual.dual,
                    "store_only": dual.store_only,
                    "mount_error": dual.mount_error,
                },
            )
        return HealthCheckResult(
            name=FAIL_ON_DUAL_PRESENCE_POOR,
            level="unknown",
            message="Insufficient dual/store_only samples for ratio",
            details={"counted": dual.counted, "dual": dual.dual, "store_only": dual.store_only},
        )
    if ratio is not None and ratio < thr and dual.ready_for_cloud_filter is False:
        return HealthCheckResult(
            name=FAIL_ON_DUAL_PRESENCE_POOR,
            level="fail",
            message=(
                f"Cloud-safe dual ratio {ratio:.2f} below {thr:.2f} "
                f"(dual={dual.dual} store_only={dual.store_only})"
            ),
            details={
                "ratio": ratio,
                "threshold": thr,
                "dual": dual.dual,
                "store_only": dual.store_only,
                "ready_for_cloud_filter": dual.ready_for_cloud_filter,
            },
        )
    if ratio is not None and ratio < thr:
        return HealthCheckResult(
            name=FAIL_ON_DUAL_PRESENCE_POOR,
            level="fail",
            message=(
                f"Cloud-safe dual ratio {ratio:.2f} below {thr:.2f} "
                f"(dual={dual.dual} store_only={dual.store_only})"
            ),
            details={
                "ratio": ratio,
                "threshold": thr,
                "dual": dual.dual,
                "store_only": dual.store_only,
            },
        )
    return HealthCheckResult(
        name=FAIL_ON_DUAL_PRESENCE_POOR,
        level="ok",
        message=(
            "Dual-presence sample ratio ok"
            + (f" ({ratio:.2f})" if ratio is not None else "")
        ),
        details={
            "ratio": ratio,
            "threshold": thr,
            "dual": dual.dual,
            "store_only": dual.store_only,
            "ready_for_cloud_filter": dual.ready_for_cloud_filter,
        },
    )


def build_health_checks(
    *,
    inventory: InventoryIntegrity,
    scan_freshness: Sequence[RootScanFreshness],
    stash: StashHealth,
    adapters: AdapterFreshness,
    fp: FPSection,
    attached_imports: Sequence[AttachedImportHealth],
    mounts: Sequence[MountProbe],
    rollups: HealthRollupInfo | None,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
    dual_presence: DualPresenceSection | None = None,
) -> list[HealthCheckResult]:
    """Build the stable named check list for a report composition."""
    checks: list[HealthCheckResult] = [
        check_stale_scan(scan_freshness, thresholds=thresholds),
        check_broken_audit(inventory),
        check_stash_overdue(stash, thresholds=thresholds),
        check_fp_not_ready(fp),
        check_rollup_stale(inventory, rollups, thresholds=thresholds),
        check_dual_presence_poor(dual_presence, thresholds=thresholds),
    ]
    # Soft adapter signal (not a default fail-on token)
    if adapters.level == "fail":
        checks.append(
            HealthCheckResult(
                name="adapter_stale",
                level="fail",
                message="Adapter end rows exceed max age",
                details={},
            )
        )
    elif adapters.level == "warn":
        checks.append(
            HealthCheckResult(
                name="adapter_stale",
                level="warn",
                message="Adapter end rows aging",
                details={},
            )
        )
    # Attached imports
    attached_fails = [a for a in attached_imports if a.level == "fail"]
    attached_warns = [a for a in attached_imports if a.level == "warn"]
    if attached_fails:
        checks.append(
            HealthCheckResult(
                name="attached_stale",
                level="fail",
                message=f"{len(attached_fails)} attached import(s) failing",
                details={
                    "machine_ids": [a.machine_id for a in attached_fails],
                },
            )
        )
    elif attached_warns:
        checks.append(
            HealthCheckResult(
                name="attached_stale",
                level="warn",
                message=f"{len(attached_warns)} attached import(s) stale or unverified",
                details={
                    "machine_ids": [a.machine_id for a in attached_warns],
                },
            )
        )
    # Mounts
    missing = [m for m in mounts if m.level == "fail"]
    low = [m for m in mounts if m.level == "warn"]
    if missing:
        checks.append(
            HealthCheckResult(
                name="mount_missing",
                level="fail",
                message=f"{len(missing)} critical mount(s) missing",
                details={"roots": [m.root for m in missing]},
            )
        )
    if low:
        checks.append(
            HealthCheckResult(
                name="mount_low",
                level="warn",
                message=f"{len(low)} mount(s) low free space or high latency",
                details={"roots": [m.root for m in low]},
            )
        )
    return checks


def compute_overall(checks: Sequence[HealthCheckResult]) -> HealthLevel:
    """Overall estate level from named checks (fail > warn > unknown > ok)."""
    if not checks:
        return "unknown"
    return worst_level(c.level for c in checks)


def evaluate_fail_on(
    report: EstateHealthReport,
    fail_on: frozenset[str] | set[str] | Sequence[str],
    *,
    thresholds: HealthThresholds | None = None,
) -> list[HealthCheckResult]:
    """Return checks among ``fail_on`` that are at level ``fail``.

    Rebuilds checks from report sections when ``report.checks`` is empty
    so unit tests can construct minimal reports.
    """
    tokens = frozenset(fail_on)
    thr = thresholds or DEFAULT_THRESHOLDS
    checks: Sequence[HealthCheckResult] = report.checks
    if not checks:
        checks = build_health_checks(
            inventory=report.inventory,
            scan_freshness=report.scan_freshness,
            stash=report.stash,
            adapters=report.adapters,
            fp=report.fp,
            attached_imports=report.attached_imports,
            mounts=report.mounts,
            rollups=report.rollups,
            thresholds=thr,
            dual_presence=report.dual_presence,
        )
    failed: list[HealthCheckResult] = []
    for c in checks:
        if c.name in tokens and c.level == "fail":
            failed.append(c)
    return failed


def validate_fail_on_tokens(tokens: Iterable[str]) -> list[str]:
    """Return unknown token names (empty if all known)."""
    return sorted({t for t in tokens if t not in KNOWN_FAIL_ON_TOKENS})


def root_scan_level(
    age: float | None,
    *,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
    has_finished: bool,
) -> HealthLevel:
    """Level for one root's finished-scan age."""
    if not has_finished or age is None:
        return "fail"
    return level_for_age(age, thresholds.scan_max_age_hours, missing_level="fail")


def attached_import_level(
    *,
    payload_exists: bool,
    import_age_hours: float | None,
    chain_verified_at: str | None,
    chain_verify_age_hours: float | None,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
) -> tuple[HealthLevel, str]:
    """Level + message for one attached inventory row."""
    if not payload_exists:
        return "fail", "Attached payload file missing"
    if import_age_hours is not None and import_age_hours > thresholds.attached_max_age_hours:
        return (
            "warn",
            f"Import age {import_age_hours:.1f}h exceeds "
            f"{thresholds.attached_max_age_days}d",
        )
    if chain_verified_at is None:
        return "warn", "Chain never verified for attached inventory"
    if (
        chain_verify_age_hours is not None
        and chain_verify_age_hours > thresholds.attached_max_age_hours
    ):
        return (
            "warn",
            f"Chain verify age {chain_verify_age_hours:.1f}h exceeds threshold",
        )
    return "ok", "Attached import fresh"


def adapter_run_level(
    age_hours_val: float | None,
    *,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
    present: bool,
) -> HealthLevel:
    if not present:
        return "unknown"
    if age_hours_val is None:
        return "unknown"
    if age_hours_val > thresholds.adapter_max_age_hours:
        return "warn"  # soft by default
    return "ok"


__all__ = [
    "age_hours",
    "adapter_run_level",
    "attached_import_level",
    "build_health_checks",
    "check_broken_audit",
    "check_dual_presence_poor",
    "check_fp_not_ready",
    "check_rollup_stale",
    "check_stale_scan",
    "check_stash_overdue",
    "compute_overall",
    "evaluate_fail_on",
    "free_space_level",
    "latency_level",
    "level_for_age",
    "parse_iso_to_utc",
    "root_scan_level",
    "validate_fail_on_tokens",
    "worst_level",
]
