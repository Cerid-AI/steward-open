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
    EstateSection,
    FPDomainHealth,
    FPSection,
    HealthCheckResult,
    HealthLevel,
    HealthRollupInfo,
    InventoryIntegrity,
    MountProbe,
    RootScanFreshness,
    StashHealth,
    UnaccountedSpace,
)
from steward.core.health.thresholds import (
    DEFAULT_THRESHOLDS,
    FAIL_ON_BROKEN_AUDIT,
    FAIL_ON_DUAL_PRESENCE_POOR,
    FAIL_ON_ESTATE_BINDING,
    FAIL_ON_FOREIGN_ATTACH,
    FAIL_ON_FP_NOT_READY,
    FAIL_ON_FP_SYNC_STUCK,
    FAIL_ON_MOUNT_LOW,
    FAIL_ON_MOUNT_MISSING,
    FAIL_ON_ROLLUP_STALE,
    FAIL_ON_STALE_SCAN,
    FAIL_ON_STASH_OVERDUE,
    FAIL_ON_UNACCOUNTED_SPACE,
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
    """Grade free space against the absolute and ratio floors.

    Returns "fail" below the fail floors, "warn" below the warn floors,
    "unknown" when the reading is missing or impossible.

    Two things this deliberately does NOT do:

    - It does not cap at "warn". It used to, which meant a volume with hours
      of runway left graded the same as one merely getting full. See the
      threshold rationale in ``thresholds.py``.
    - It does not score an impossible reading as healthy. ``shutil.disk_usage``
      returns nonsense on some NFS mounts here — /Volumes/Backup reports more
      free bytes than total, which computes to -2388% used. The old code read
      the huge ``free`` value, cleared both floors and returned "ok", so two
      network tiers were reporting healthy while nothing about them was
      being measured. An unmeasurable volume is "unknown", never "ok".
    """
    if free_bytes is None:
        return "unknown"
    ratio: float | None = None
    if total_bytes is not None and total_bytes > 0:
        if free_bytes > total_bytes:
            return "unknown"
        ratio = free_bytes / float(total_bytes)
    if free_bytes < thresholds.free_bytes_fail or (
        ratio is not None and ratio < thresholds.free_ratio_fail
    ):
        return "fail"
    if free_bytes < thresholds.free_bytes_min or (
        ratio is not None and ratio < thresholds.free_ratio_min
    ):
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


def fp_domain_level(
    error_generation: int | None,
    *,
    thresholds: HealthThresholds,
) -> HealthLevel:
    """Grade one File Provider domain by its failed-cycle counter.

    ``error_generation`` is fileproviderd's own monotonic count of failed
    sync cycles for the domain; it resets only on domain rebuild. A missing
    value means the dump could not be parsed for this domain — "unknown",
    never "ok", for the same reason an unmeasurable volume never grades
    healthy.
    """
    if error_generation is None:
        return "unknown"
    if error_generation >= thresholds.fp_error_generation_fail:
        return "fail"
    if error_generation >= thresholds.fp_error_generation_warn:
        return "warn"
    return "ok"


def check_fp_sync_stuck(
    domains: Sequence[FPDomainHealth] | None,
    *,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
) -> HealthCheckResult:
    """The sync-loop gate this estate lacked for 46 days.

    On 2026-08-16 the iCloud Drive domain was found at error generation 628
    with thousands of ``itemNotFound/missingLastKnownVersion`` fetch errors —
    a retry loop running since ~July 1, staging transfer data into
    nsurlsessiond's DataVault (unreachable by any du) at ~10-20 GB/day until
    the boot volume was hours from full. `fp status` could not see it: that
    check asks whether the Dropbox STORE LAYOUT is retire-ready, not whether
    any domain's sync engine converges.

    ``None`` means the collector did not run — "skipped", which is what makes
    this token safe in the default fail-on set.
    """
    if domains is None:
        return HealthCheckResult(
            name=FAIL_ON_FP_SYNC_STUCK,
            level="skipped",
            message="File Provider domain dump not collected (see --fp-domains)",
            details={},
        )
    if not domains:
        # An empty parse of a dump that DID run is indistinguishable from a
        # parser that silently stopped matching — the format drifts across
        # macOS releases. Never let that read as healthy.
        return HealthCheckResult(
            name=FAIL_ON_FP_SYNC_STUCK,
            level="unknown",
            message="fileproviderctl dump yielded no parseable domains",
            details={"domains": 0},
        )
    worst = worst_level(d.level for d in domains)
    looping = [d for d in domains if d.level in ("warn", "fail")]
    if looping:
        return HealthCheckResult(
            name=FAIL_ON_FP_SYNC_STUCK,
            level=worst,
            message=(
                f"{len(looping)} File Provider domain(s) in a sync-error loop"
            ),
            details={
                "domains": {
                    d.domain: {
                        "error_generation": d.error_generation,
                        "stuck_errors": d.stuck_errors,
                        "pending_indexable": d.pending_indexable,
                    }
                    for d in looping
                },
                "warn_at": thresholds.fp_error_generation_warn,
                "fail_at": thresholds.fp_error_generation_fail,
            },
        )
    return HealthCheckResult(
        name=FAIL_ON_FP_SYNC_STUCK,
        level="ok" if worst in ("ok", "skipped") else worst,
        message=f"{len(domains)} File Provider domain(s) converging",
        details={"domains": len(domains)},
    )


def unaccounted_level(
    gap_bytes: int | None,
    *,
    thresholds: HealthThresholds,
) -> HealthLevel:
    """Grade the df-vs-reachable gap.

    Floors are ABSOLUTE, not proportional: DataVaults and filesystem
    metadata — the legitimate part of any gap — are a roughly fixed cost on
    a macOS volume, not one that scales with disk size the way content does.
    A negative gap (traversal found more than allocated, e.g. hardlink
    double-counting) is measurement noise and grades ok.
    """
    if gap_bytes is None:
        return "unknown"
    if gap_bytes >= thresholds.unaccounted_fail_bytes:
        return "fail"
    if gap_bytes >= thresholds.unaccounted_warn_bytes:
        return "warn"
    return "ok"


def check_unaccounted_space(
    unaccounted: UnaccountedSpace | None,
    *,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
) -> HealthCheckResult:
    """Alert when allocated space diverges from what traversal can reach.

    The 2026-08-16 incident in one number: df reported 743 GiB used while
    root's du could reach 293 GiB — a 441 GiB gap that had grown for 46 days
    with every directory listing looking innocent, because the data sat in a
    DataVault no userland traversal may enter. Twelve hypotheses were tested
    by hand to find it. This check is that hunt, automated to its first
    step: measure the gap, alarm on magnitude.

    ``None`` means the (deliberately slow, opt-in) walk did not run —
    "skipped", never "ok".
    """
    if unaccounted is None:
        return HealthCheckResult(
            name=FAIL_ON_UNACCOUNTED_SPACE,
            level="skipped",
            message="df-vs-reachable walk not run (see --unaccounted; slow)",
            details={},
        )
    if unaccounted.gap_bytes is None:
        return HealthCheckResult(
            name=FAIL_ON_UNACCOUNTED_SPACE,
            level="unknown",
            message=unaccounted.error or "gap could not be measured",
            details={"root": unaccounted.root},
        )
    level = unaccounted_level(unaccounted.gap_bytes, thresholds=thresholds)
    gib = unaccounted.gap_bytes / 1024**3
    return HealthCheckResult(
        name=FAIL_ON_UNACCOUNTED_SPACE,
        level=level,
        message=(
            f"{gib:.0f} GiB allocated on {unaccounted.root} is unreachable by "
            "traversal (DataVaults / fs metadata / staged transfers)"
            if level != "ok"
            else f"unaccounted space on {unaccounted.root} within normal bounds ({gib:.0f} GiB)"
        ),
        details={
            "root": unaccounted.root,
            "used_bytes": unaccounted.used_bytes,
            "reachable_bytes": unaccounted.reachable_bytes,
            "gap_bytes": unaccounted.gap_bytes,
            "warn_bytes": thresholds.unaccounted_warn_bytes,
            "fail_bytes": thresholds.unaccounted_fail_bytes,
        },
    )


def check_mount_missing(mounts: Sequence[MountProbe]) -> HealthCheckResult:
    """Roots that are not present at all.

    Kept strictly separate from capacity. Until 2026-08-16 this check simply
    collected every probe at level ``fail``, which was fine while ``fail``
    could only mean "critical mount absent". Once free space gained a fail
    band, a disk that was merely FULL landed here and got reported as
    "critical mount(s) missing" — a present, mounted, working volume described
    as gone. Presence is the discriminator, not the level.
    """
    absent = [m for m in mounts if not m.present]
    if not mounts:
        return HealthCheckResult(
            name=FAIL_ON_MOUNT_MISSING,
            level="skipped",
            message="mount probes not run",
            details={"probed": 0},
        )
    if not absent:
        return HealthCheckResult(
            name=FAIL_ON_MOUNT_MISSING,
            level="ok",
            message=f"all {len(mounts)} probed root(s) present",
            details={"probed": len(mounts)},
        )
    critical = [m for m in absent if m.level == "fail"]
    return HealthCheckResult(
        name=FAIL_ON_MOUNT_MISSING,
        level="fail" if critical else "warn",
        message=f"{len(absent)} probed root(s) not present",
        details={"roots": [m.root for m in absent]},
    )


def check_mount_low(mounts: Sequence[MountProbe]) -> HealthCheckResult:
    """Capacity verdict over the present mounts — the `mount_low` gate.

    ADR-0017 deferred this token in v1, so a mount grading ``fail`` could not
    fail the estate gate: `steward health check` exited 0 with the boot volume
    hours from filling. That is the gap this closes.

    Absence of probes is reported as ``skipped``, never ``ok``. `health check`
    defaults to ``--no-probes``, so "no mounts in the report" is the common
    case and it means capacity was not measured — which must not be
    indistinguishable from every volume being healthy.
    """
    present = [m for m in mounts if m.present]
    if not present:
        return HealthCheckResult(
            name=FAIL_ON_MOUNT_LOW,
            level="skipped",
            message="no live mount probes — capacity not measured (see --probes)",
            details={"probed": len(mounts)},
        )
    failing = [m for m in present if m.level == "fail"]
    degraded = [m for m in present if m.level in ("warn", "unknown")]
    if failing:
        return HealthCheckResult(
            name=FAIL_ON_MOUNT_LOW,
            level="fail",
            message=f"{len(failing)} mount(s) critically low on free space",
            details={
                "roots": [m.root for m in failing],
                "free_bytes": {m.root: m.free_bytes for m in failing},
            },
        )
    if degraded:
        return HealthCheckResult(
            name=FAIL_ON_MOUNT_LOW,
            level="warn",
            message=f"{len(degraded)} mount(s) low on free space or unmeasurable",
            details={"roots": [m.root for m in degraded]},
        )
    return HealthCheckResult(
        name=FAIL_ON_MOUNT_LOW,
        level="ok",
        message=f"{len(present)} mount(s) with healthy free space",
        details={"probed": len(present)},
    )


def check_foreign_attach(estate: EstateSection) -> HealthCheckResult:
    """Fail when a volume reserved for another host is mounted or attached here.

    Two hosts attaching one sparsebundle at once corrupts it, and a ``forbid``
    mount means this host can see (and scan, or stash into) a volume it must
    never touch.
    """
    if estate.foreign is None:
        reason = (
            "this machine is not an estate host"
            if estate.host_id is None
            else "attach probes not run (see --probes)"
        )
        return HealthCheckResult(name=FAIL_ON_FOREIGN_ATTACH, level="skipped", message=reason, details={})
    if not estate.foreign:
        return HealthCheckResult(
            name=FAIL_ON_FOREIGN_ATTACH,
            level="ok",
            message="no forbidden mounts or foreign exclusive images",
            details={"host": estate.host_id},
        )
    return HealthCheckResult(
        name=FAIL_ON_FOREIGN_ATTACH,
        level="fail",
        message="; ".join(f.message for f in estate.foreign),
        details={
            "host": estate.host_id,
            "found": [{"kind": f.kind, "volume": f.volume_id, "path": f.path} for f in estate.foreign],
        },
    )


def check_estate_binding(estate: EstateSection) -> HealthCheckResult:
    """Fail when this machine is not an estate host, or its inventory.db is not the pinned one."""
    if estate.host_id is None:
        return HealthCheckResult(
            name=FAIL_ON_ESTATE_BINDING,
            level="fail",
            message=f"this machine is not an estate host: {estate.host_reason}",
            details={"host": None},
        )
    level: HealthLevel = "fail" if estate.binding in ("mismatch", "missing") else "ok"
    return HealthCheckResult(
        name=FAIL_ON_ESTATE_BINDING,
        level=level,
        message=estate.binding_message or f"binding {estate.binding}",
        details={"host": estate.host_id, "binding": estate.binding},
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
    fp_domains: Sequence[FPDomainHealth] | None = None,
    unaccounted: UnaccountedSpace | None = None,
    estate: EstateSection | None = None,
) -> list[HealthCheckResult]:
    """Build the stable named check list for a report composition.

    The estate checks are only graded when an estate file exists, so a
    single-host report keeps its exact check list.
    """
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
    checks.append(check_mount_missing(mounts))
    checks.append(check_mount_low(mounts))
    checks.append(check_fp_sync_stuck(fp_domains, thresholds=thresholds))
    checks.append(check_unaccounted_space(unaccounted, thresholds=thresholds))
    if estate is not None:
        checks.append(check_foreign_attach(estate))
        checks.append(check_estate_binding(estate))
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
            fp_domains=report.fp_domains,
            unaccounted=report.unaccounted,
            estate=report.estate,
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
    "check_estate_binding",
    "check_foreign_attach",
    "check_fp_not_ready",
    "check_rollup_stale",
    "check_fp_sync_stuck",
    "check_mount_low",
    "check_mount_missing",
    "check_stale_scan",
    "check_unaccounted_space",
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
