# SPDX-License-Identifier: Apache-2.0

"""Unit tests for pure estate-health evaluation (ADR-0017)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from steward.core.health import (
    DEFAULT_CHECK_FAIL_ON,
    DEFAULT_THRESHOLDS,
    FAIL_ON_BROKEN_AUDIT,
    FAIL_ON_DUAL_PRESENCE_POOR,
    FAIL_ON_ESTATE_BINDING,
    FAIL_ON_FLEET_STALE_SCAN,
    FAIL_ON_FOREIGN_ATTACH,
    FAIL_ON_FP_NOT_READY,
    FAIL_ON_FP_SYNC_STUCK,
    FAIL_ON_MOUNT_LOW,
    FAIL_ON_ROLLUP_STALE,
    FAIL_ON_STALE_SCAN,
    FAIL_ON_STASH_OVERDUE,
    FAIL_ON_UNACCOUNTED_SPACE,
    KNOWN_FAIL_ON_TOKENS,
    AdapterFreshness,
    AdapterRunHealth,
    AttachedImportHealth,
    DualPresenceSection,
    EstateHealthReport,
    FPSection,
    HealthCheckResult,
    HealthRollupInfo,
    HealthThresholds,
    InventoryIntegrity,
    MountProbe,
    RootScanFreshness,
    StashHealth,
    age_hours,
    build_health_checks,
    check_broken_audit,
    check_dual_presence_poor,
    check_fp_not_ready,
    check_mount_low,
    check_mount_missing,
    check_rollup_stale,
    check_stale_scan,
    check_stash_overdue,
    compute_overall,
    evaluate_fail_on,
    free_space_level,
    latency_level,
    level_for_age,
    root_scan_level,
    validate_fail_on_tokens,
    worst_level,
)

NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def _empty_inventory(**kwargs: object) -> InventoryIntegrity:
    base: dict[str, object] = {
        "permanodes": 1,
        "current_claims": 1,
        "scan_runs": 1,
        "audit_entries": 10,
        "machines": 1,
        "audit_ok": True,
        "audit_skipped": False,
        "counts_source": "live",
    }
    base.update(kwargs)
    return InventoryIntegrity(**base)  # type: ignore[arg-type]


def _stash(
    *,
    in_flight: int = 0,
    age_h: float | None = None,
    source: str = "live",
    overdue: bool | None = None,
) -> StashHealth:
    thr = DEFAULT_THRESHOLDS
    if overdue is None and age_h is not None and in_flight > 0:
        overdue = age_h > thr.stash_overdue_hours
    return StashHealth(
        in_flight_entries=in_flight,
        distinct_run_ids=1 if in_flight else 0,
        oldest_ts_iso=_iso(age_h) if age_h is not None else None,
        newest_ts_iso=_iso(0) if age_h is not None else None,
        age_hours_oldest=age_h,
        cooling_off_days=thr.cooling_off_days,
        grace_hours=thr.stash_grace_hours,
        overdue=overdue,
        source=source,  # type: ignore[arg-type]
        level="skipped" if source == "skipped" else "ok",
    )


def _fp(
    *,
    present: bool = True,
    ready: bool | None = True,
    problems: tuple[str, ...] = (),
) -> FPSection:
    return FPSection(
        present=present,
        layout="external_drive_fp" if present else None,
        cloud_retire_ready=ready,
        local_reclaim_ready=True if present else None,
        problems=problems,
        level="ok" if ready else "fail",
    )


def _report(
    *,
    inventory: InventoryIntegrity | None = None,
    scan: list[RootScanFreshness] | None = None,
    stash: StashHealth | None = None,
    fp: FPSection | None = None,
    rollups: HealthRollupInfo | None = None,
    checks: tuple[HealthCheckResult, ...] | None = None,
) -> EstateHealthReport:
    inv = inventory or _empty_inventory()
    stash_h = stash or _stash()
    fp_s = fp if fp is not None else _fp()
    adapters = AdapterFreshness(
        replicate=AdapterRunHealth(
            action="replicate_end",
            timestamp=_iso(1),
            age_hours=1.0,
            policy_name=None,
            level="ok",
        ),
        archive=None,
        level="ok",
    )
    scan_t = tuple(scan or [])
    if checks is None:
        checks_l = build_health_checks(
            inventory=inv,
            scan_freshness=scan_t,
            stash=stash_h,
            adapters=adapters,
            fp=fp_s,
            attached_imports=(),
            mounts=(),
            rollups=rollups,
        )
        checks = tuple(checks_l)
    return EstateHealthReport(
        generated_at=NOW.isoformat(timespec="seconds"),
        machine_id="test-machine",
        overall=compute_overall(checks),
        inventory=inv,
        scan_freshness=scan_t,
        stash=stash_h,
        adapters=adapters,
        schedule=None,
        fp=fp_s,
        attached_imports=(),
        mounts=(),
        rollups=rollups,
        checks=checks,
        notes=(),
        quick=True,
    )


# ── thresholds defaults ──────────────────────────────────────────


def test_default_thresholds_match_adr() -> None:
    t = DEFAULT_THRESHOLDS
    assert t.scan_max_age_hours == 168.0
    assert t.stash_grace_hours == 24.0
    assert t.cooling_off_days == 7
    assert t.stash_overdue_hours == 7 * 24 + 24
    assert t.adapter_max_age_hours == 168.0
    assert t.rollup_max_age_hours == 24.0
    assert t.attached_max_age_days == 30.0
    # Capacity bands per the ADR-0017 amendment of 2026-08-16 (free space
    # gained a fail band; the absolute warn floor rose from 10 to 25 GiB).
    assert t.free_bytes_min == 25 * 1024**3
    assert t.free_ratio_min == 0.08
    assert t.free_bytes_fail == 10 * 1024**3
    assert t.free_ratio_fail == 0.03
    assert t.sample_latency_warn_ms == 2000.0


def _mount(
    root: str, *, level: str = "ok", present: bool = True, free: int | None = None,
) -> MountProbe:
    return MountProbe(
        root=root,
        tier="boot",
        present=present,
        free_bytes=free,
        total_bytes=808 * 1024**3,
        sample_latency_ms=1.0,
        error=None,
        level=level,  # type: ignore[arg-type]
        message="",
    )


class TestMountLowGate:
    """`mount_low` is the capacity gate ADR-0017 deferred in v1.

    Without it `steward health check` exited 0 while the boot volume graded
    `fail` — the estate gate could not be failed by a disk about to fill.
    """

    def test_failing_mount_fails_the_check(self) -> None:
        result = check_mount_low([_mount("/", level="fail", free=20 * 1024**3)])
        assert result.level == "fail"
        assert result.name == FAIL_ON_MOUNT_LOW

    def test_warn_mount_warns(self) -> None:
        assert check_mount_low([_mount("/", level="warn")]).level == "warn"

    def test_unknown_mount_warns_not_ok(self) -> None:
        """An unmeasurable volume is not a healthy one."""
        assert check_mount_low([_mount("/", level="unknown")]).level == "warn"

    def test_healthy_mounts_are_ok(self) -> None:
        assert check_mount_low([_mount("/", level="ok")]).level == "ok"

    def test_no_probes_is_skipped_not_ok(self) -> None:
        """`health check` defaults to --no-probes. "Not measured" must never
        be indistinguishable from "every volume is fine" — that is what makes
        mount_low safe to keep in the DEFAULT fail-on set."""
        assert check_mount_low([]).level == "skipped"

    def test_absent_roots_do_not_drive_capacity(self) -> None:
        """A root that is not mounted has no capacity verdict to give."""
        assert check_mount_low([_mount("/gone", level="fail", present=False)]).level == "skipped"

    def test_mount_low_is_in_the_default_fail_on_set(self) -> None:
        assert FAIL_ON_MOUNT_LOW in DEFAULT_CHECK_FAIL_ON
        assert FAIL_ON_MOUNT_LOW in KNOWN_FAIL_ON_TOKENS


class TestMountMissingSeparation:
    def test_full_disk_is_not_reported_as_missing(self) -> None:
        """A present, working, FULL volume must not read as "mount missing".

        Before free space gained a fail band, `mount_missing` collected every
        probe at level `fail`, which was safe only while `fail` meant "absent".
        Afterwards a full disk was reported as "critical mount(s) missing" —
        a mounted, working volume described as gone.
        """
        result = check_mount_missing([_mount("/", level="fail", free=20 * 1024**3)])
        assert result.level == "ok"
        assert "present" in result.message

    def test_absent_critical_root_still_fails(self) -> None:
        result = check_mount_missing([_mount("/gone", level="fail", present=False)])
        assert result.level == "fail"

    def test_no_probes_is_skipped(self) -> None:
        assert check_mount_missing([]).level == "skipped"


def test_known_fail_on_tokens() -> None:
    assert FAIL_ON_STALE_SCAN in KNOWN_FAIL_ON_TOKENS
    assert FAIL_ON_FP_NOT_READY in KNOWN_FAIL_ON_TOKENS
    assert FAIL_ON_DUAL_PRESENCE_POOR in KNOWN_FAIL_ON_TOKENS
    # Defaults are local integrity only (ADR-0017 / 0020 / 0021 opt-in policy).
    assert FAIL_ON_FP_NOT_READY not in DEFAULT_CHECK_FAIL_ON
    assert FAIL_ON_DUAL_PRESENCE_POOR not in DEFAULT_CHECK_FAIL_ON
    assert FAIL_ON_FLEET_STALE_SCAN not in DEFAULT_CHECK_FAIL_ON
    assert DEFAULT_CHECK_FAIL_ON == frozenset(
        {
            FAIL_ON_STALE_SCAN,
            FAIL_ON_BROKEN_AUDIT,
            FAIL_ON_STASH_OVERDUE,
            FAIL_ON_ROLLUP_STALE,
            # Capacity, sync-loop and unaccounted-space joined the defaults
            # in the 2026-08-16 ADR-0017 amendment. Unlike the FP-layout and
            # fleet tokens they cannot false-red on an unusual host: each
            # reports `skipped` until its collector actually ran (probes /
            # --fp-domains / --unaccounted), so "not measured" never grades
            # as "fine" and never as "broken" either.
            FAIL_ON_MOUNT_LOW,
            FAIL_ON_FP_SYNC_STUCK,
            FAIL_ON_UNACCOUNTED_SPACE,
            # Graded only when an estate file exists.
            FAIL_ON_FOREIGN_ATTACH,
            FAIL_ON_ESTATE_BINDING,
        }
    )
    assert validate_fail_on_tokens(["stale_scan", "nope"]) == ["nope"]


# ── pure age / level helpers ─────────────────────────────────────


def test_age_hours_and_level_for_age() -> None:
    assert age_hours(_iso(10), now=NOW) == pytest.approx(10.0)
    assert age_hours(None, now=NOW) is None
    assert level_for_age(10.0, 168.0) == "ok"
    assert level_for_age(200.0, 168.0) == "fail"
    assert level_for_age(None, 168.0) == "fail"
    assert level_for_age(None, 168.0, missing_level="unknown") == "unknown"


def test_worst_level_ordering() -> None:
    assert worst_level(["ok", "warn", "fail"]) == "fail"
    assert worst_level(["ok", "skipped", "unknown"]) == "unknown"
    assert worst_level([]) == "unknown"


def test_free_space_and_latency() -> None:
    thr = DEFAULT_THRESHOLDS
    assert free_space_level(50 * 1024**3, 100 * 1024**3, thresholds=thr) == "ok"
    # 5 GiB is below the 10 GiB absolute fail floor. This asserted "warn"
    # until 2026-08-16, when free space gained a fail band.
    assert free_space_level(5 * 1024**3, 100 * 1024**3, thresholds=thr) == "fail"
    # 2% of a large volume: caught by the ratio fail floor, not the absolute.
    assert free_space_level(20 * 1024**3, 1000 * 1024**3, thresholds=thr) == "fail"
    assert free_space_level(None, None, thresholds=thr) == "unknown"
    assert latency_level(100.0, thresholds=thr) == "ok"
    assert latency_level(2500.0, thresholds=thr) == "warn"


def test_free_space_warn_band_sits_between_ok_and_fail() -> None:
    thr = DEFAULT_THRESHOLDS
    # 5% of a large volume: under the 8% warn ratio, over the 3% fail ratio.
    assert free_space_level(50 * 1024**3, 1000 * 1024**3, thresholds=thr) == "warn"
    # 20 GiB absolute: under the 25 GiB warn floor, over the 10 GiB fail floor.
    assert free_space_level(20 * 1024**3, 100 * 1024**3, thresholds=thr) == "warn"


def test_free_space_live_boot_volume_condition_is_fail() -> None:
    """The 2026-08-16 Mac Pro reading must not grade as a warning.

    20.4 GiB free of 808 GiB (2.5%) is under a day of runway at the observed
    ~20 GB/day burn, on the volume whose filling took the whole CI fleet and
    the Docker VM down three days earlier.
    """
    level = free_space_level(
        int(20.43 * 1024**3), int(808.39 * 1024**3), thresholds=DEFAULT_THRESHOLDS,
    )
    assert level == "fail"


def test_free_space_impossible_reading_is_unknown_not_ok() -> None:
    """More free than total cannot be scored — and must never read healthy.

    `shutil.disk_usage` returns exactly this on the NFS tiers here
    (/Volumes/Backup, /Volumes/Level_3a report -2388% used). The huge bogus
    `free` cleared both floors, so two network tiers reported "ok" while
    nothing about them was being measured.
    """
    thr = DEFAULT_THRESHOLDS
    assert free_space_level(815 * 1024**3, 32 * 1024**3, thresholds=thr) == "unknown"


def test_free_space_fail_floors_are_tighter_than_warn() -> None:
    """Guards the ordering invariant the two bands depend on."""
    thr = DEFAULT_THRESHOLDS
    assert thr.free_bytes_fail < thr.free_bytes_min
    assert thr.free_ratio_fail < thr.free_ratio_min


# ── named checks ─────────────────────────────────────────────────


def test_stale_scan_empty_roots_fail() -> None:
    c = check_stale_scan([])
    assert c.level == "fail"
    assert c.name == FAIL_ON_STALE_SCAN


def test_stale_scan_age_boundary() -> None:
    thr = HealthThresholds(scan_max_age_hours=168.0)
    fresh = RootScanFreshness(
        root_path="/Volumes/Level 2",
        tier="L2",
        scan_run_id=1,
        finished_at=_iso(167.0),
        age_hours=167.0,
        level=root_scan_level(167.0, thresholds=thr, has_finished=True),
    )
    assert check_stale_scan([fresh], thresholds=thr).level == "ok"

    stale = RootScanFreshness(
        root_path="/Volumes/Level 2",
        tier="L2",
        scan_run_id=2,
        finished_at=_iso(169.0),
        age_hours=169.0,
        level="fail",
    )
    assert check_stale_scan([stale], thresholds=thr).level == "fail"

    missing = RootScanFreshness(
        root_path="/tmp/x",
        tier=None,
        scan_run_id=None,
        finished_at=None,
        age_hours=None,
        level="fail",
    )
    assert check_stale_scan([missing], thresholds=thr).level == "fail"


def test_broken_audit_matrix() -> None:
    ok = check_broken_audit(_empty_inventory(audit_ok=True, audit_skipped=False))
    assert ok.level == "ok"
    skipped = check_broken_audit(_empty_inventory(audit_ok=None, audit_skipped=True))
    assert skipped.level == "skipped"
    broken = check_broken_audit(
        _empty_inventory(audit_ok=False, audit_skipped=False, audit_error="hash break")
    )
    assert broken.level == "fail"
    assert "hash break" in broken.message


def test_stash_overdue_matrix() -> None:
    thr = DEFAULT_THRESHOLDS
    limit = thr.stash_overdue_hours  # 192h
    assert check_stash_overdue(_stash(source="skipped"), thresholds=thr).level == "skipped"
    assert check_stash_overdue(_stash(in_flight=0), thresholds=thr).level == "ok"
    under = check_stash_overdue(_stash(in_flight=2, age_h=limit - 1), thresholds=thr)
    assert under.level == "ok"
    over = check_stash_overdue(_stash(in_flight=2, age_h=limit + 1), thresholds=thr)
    assert over.level == "fail"


def test_fp_not_ready_matrix() -> None:
    assert check_fp_not_ready(_fp(present=False)).level == "skipped"
    assert check_fp_not_ready(_fp(ready=True)).level == "ok"
    assert check_fp_not_ready(_fp(ready=False, problems=("mount missing",))).level == "fail"
    assert check_fp_not_ready(_fp(ready=False)).level == "fail"


def test_rollup_stale_matrix() -> None:
    thr = HealthThresholds(rollup_max_age_hours=24.0)
    live = check_rollup_stale(
        _empty_inventory(counts_source="live"),
        None,
        thresholds=thr,
    )
    assert live.level == "ok"

    fresh_cache = check_rollup_stale(
        _empty_inventory(
            counts_source="rollup",
            rollup_used_cache=True,
            rollup_age_hours=12.0,
            rollup_refreshed_at=_iso(12),
        ),
        HealthRollupInfo(used_cache=True, refreshed_at=_iso(12), age_hours=12.0),
        thresholds=thr,
    )
    assert fresh_cache.level == "ok"

    stale_cache = check_rollup_stale(
        _empty_inventory(
            counts_source="rollup",
            rollup_used_cache=True,
            rollup_age_hours=48.0,
            rollup_refreshed_at=_iso(48),
        ),
        HealthRollupInfo(used_cache=True, refreshed_at=_iso(48), age_hours=48.0),
        thresholds=thr,
    )
    assert stale_cache.level == "fail"

    unknown = check_rollup_stale(
        _empty_inventory(counts_source="unknown"),
        None,
        thresholds=thr,
    )
    assert unknown.level == "unknown"


# ── evaluate_fail_on ─────────────────────────────────────────────


def test_evaluate_fail_on_filters_only_requested_fails() -> None:
    report = _report(
        scan=[
            RootScanFreshness(
                root_path="/x",
                tier=None,
                scan_run_id=None,
                finished_at=None,
                age_hours=None,
                level="fail",
            )
        ],
        fp=_fp(ready=False, problems=("bad",)),
    )
    failed_default = evaluate_fail_on(report, DEFAULT_CHECK_FAIL_ON)
    names = {c.name for c in failed_default}
    assert FAIL_ON_STALE_SCAN in names
    # fp_not_ready is fail on report but not in default fail-on
    assert FAIL_ON_FP_NOT_READY not in names

    with_fp = evaluate_fail_on(report, frozenset({FAIL_ON_FP_NOT_READY}))
    assert len(with_fp) == 1
    assert with_fp[0].name == FAIL_ON_FP_NOT_READY


def test_evaluate_fail_on_skips_do_not_fail() -> None:
    report = _report(
        inventory=_empty_inventory(audit_ok=None, audit_skipped=True),
        stash=_stash(source="skipped"),
        scan=[
            RootScanFreshness(
                root_path="/y",
                tier=None,
                scan_run_id=1,
                finished_at=_iso(1),
                age_hours=1.0,
                level="ok",
            )
        ],
    )
    failed = evaluate_fail_on(report, DEFAULT_CHECK_FAIL_ON)
    assert failed == []


def test_evaluate_fail_on_rebuilds_when_checks_empty() -> None:
    inv = _empty_inventory(audit_ok=False, audit_error="broken")
    report = EstateHealthReport(
        generated_at=NOW.isoformat(timespec="seconds"),
        machine_id="m",
        overall="unknown",
        inventory=inv,
        scan_freshness=(
            RootScanFreshness(
                root_path="/z",
                tier=None,
                scan_run_id=1,
                finished_at=_iso(1),
                age_hours=1.0,
                level="ok",
            ),
        ),
        stash=_stash(),
        adapters=AdapterFreshness(replicate=None, archive=None),
        schedule=None,
        fp=_fp(),
        attached_imports=(),
        mounts=(),
        rollups=None,
        checks=(),  # force rebuild
        notes=(),
    )
    failed = evaluate_fail_on(report, frozenset({FAIL_ON_BROKEN_AUDIT}))
    assert len(failed) == 1
    assert failed[0].level == "fail"


def test_compute_overall_from_checks() -> None:
    assert compute_overall([]) == "unknown"
    assert (
        compute_overall(
            [
                HealthCheckResult("a", "ok", "ok"),
                HealthCheckResult("b", "warn", "w"),
            ]
        )
        == "warn"
    )
    assert (
        compute_overall(
            [
                HealthCheckResult("a", "warn", "w"),
                HealthCheckResult("b", "fail", "f"),
            ]
        )
        == "fail"
    )


def test_build_health_checks_includes_soft_sections() -> None:
    checks = build_health_checks(
        inventory=_empty_inventory(),
        scan_freshness=[
            RootScanFreshness(
                root_path="/Volumes/Level 1",
                tier="L1",
                scan_run_id=1,
                finished_at=_iso(1),
                age_hours=1.0,
                level="ok",
            )
        ],
        stash=_stash(),
        adapters=AdapterFreshness(
            replicate=AdapterRunHealth(
                "replicate_end", _iso(200), 200.0, None, "warn"
            ),
            archive=None,
            level="warn",
        ),
        fp=_fp(),
        attached_imports=[
            AttachedImportHealth(
                machine_id="other",
                file_path="/tmp/x.db",
                imported_at=_iso(1),
                import_age_hours=1.0,
                chain_verified_at=None,
                chain_verify_age_hours=None,
                payload_exists=False,
                level="fail",
                message="missing",
            )
        ],
        mounts=[
            MountProbe(
                root="/Volumes/DropboxStorage",
                tier="DropboxStorage",
                present=False,
                level="fail",
                message="missing",
            )
        ],
        rollups=None,
    )
    names = {c.name for c in checks}
    assert FAIL_ON_STALE_SCAN in names
    assert "adapter_stale" in names
    assert "attached_stale" in names
    assert "mount_missing" in names


def test_dual_presence_poor_fail_and_ok() -> None:
    poor = DualPresenceSection(
        present=True,
        counted=10,
        dual=2,
        store_only=8,
        cloud_safe_sample_ratio=0.2,
        ready_for_cloud_filter=False,
        level="fail",
    )
    r = check_dual_presence_poor(poor)
    assert r.name == FAIL_ON_DUAL_PRESENCE_POOR
    assert r.level == "fail"

    good = DualPresenceSection(
        present=True,
        counted=10,
        dual=8,
        store_only=2,
        cloud_safe_sample_ratio=0.8,
        ready_for_cloud_filter=True,
        level="ok",
    )
    r2 = check_dual_presence_poor(good)
    assert r2.level == "ok"

    skipped = check_dual_presence_poor(None)
    assert skipped.level == "skipped"
