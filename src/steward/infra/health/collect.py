# SPDX-License-Identifier: Apache-2.0

"""Compose estate health from status / FP / schedule / mounts (ADR-0017).

Reuses :func:`collect_status` and :func:`collect_fp_status`; does not
duplicate claim-table SQL. Scan freshness uses a single
``scan_runs GROUP BY root_path`` query.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from steward.core.health.evaluate import (
    adapter_run_level,
    age_hours,
    attached_import_level,
    build_health_checks,
    compute_overall,
    root_scan_level,
    worst_level,
)
from steward.core.health.model import (
    AdapterFreshness,
    AdapterRunHealth,
    AttachedImportHealth,
    DualPresenceSection,
    EstateHealthReport,
    FleetHealthSummary,
    FPSection,
    HealthCheckResult,
    HealthLevel,
    HealthRollupInfo,
    InventoryIntegrity,
    MountProbe,
    RootScanFreshness,
    ScheduleHealth,
    ScheduleTemplateHealth,
    StashHealth,
)
from steward.core.health.thresholds import DEFAULT_THRESHOLDS, HealthThresholds
from steward.core.tiers import classify_tier
from steward.infra.db.admin import resolve_machine_id
from steward.infra.db.connect import connect
from steward.infra.fp_status import collect_fp_status
from steward.infra.health.probes import probe_mounts
from steward.infra.observability import log_swallowed_error
from steward.infra.status import StatusReport, collect_status


def collect_estate_health(
    *,
    db_path: Path,
    quick: bool = True,
    include_imports: bool = False,
    probes: bool = True,
    refresh_rollups: bool = False,
    thresholds: HealthThresholds | None = None,
    now: datetime | None = None,
    include_fp: bool = True,
    include_schedule: bool = True,
    include_dual_presence: bool | None = None,
    include_fleet: bool | None = None,
) -> EstateHealthReport:
    """Compose an :class:`EstateHealthReport` for the local estate.

    Parameters
    ----------
    quick:
        Skip full audit walk and stash CTE (status ``--quick`` semantics).
        Stash checks become ``skipped`` unless a future meta cache lands.
    probes:
        Live mount free/total + latency (capped roots).
    include_fp:
        Call lightweight :func:`collect_fp_status`.
    include_schedule:
        Soft-import schedule templates; list installed presence only.
    include_dual_presence:
        Collect bounded dual-presence sample (ADR-0020). Default: True when
        ``probes`` is True or ``quick`` is False; False on cheap quick path
        unless explicitly enabled.
    """
    thr = thresholds or DEFAULT_THRESHOLDS
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    generated_at = ref.isoformat(timespec="seconds")
    target = Path(db_path).expanduser()

    machine_id = "unknown"
    try:
        machine_id = resolve_machine_id(target)
    except Exception as exc:  # noqa: BLE001 — best-effort health section
        log_swallowed_error(
            "health.collect.machine_id",
            exc,
            context={"db_path": str(target)},
        )

    status = collect_status(
        db_path=target,
        include_imports=include_imports,
        quick=quick,
        refresh_rollups=refresh_rollups,
        rollup_max_age_seconds=int(thr.rollup_max_age_hours * 3600),
    )

    inventory = _inventory_from_status(status, thr=thr, now=ref)
    rollups = _rollups_from_status(status, now=ref)

    scan_freshness = _collect_scan_freshness(target, thr=thr, now=ref)
    stash = _stash_from_status(status, thr=thr, now=ref, quick=quick)
    adapters = _adapters_from_status(status, thr=thr, now=ref)

    schedule: ScheduleHealth | None = None
    if include_schedule:
        schedule = _collect_schedule_health(quick=quick)

    fp = _collect_fp_section(include_fp=include_fp)

    if include_dual_presence is None:
        # ADR-0020 cheap default: when FP is collected, include fixed-rel
        # dual-presence sample (no full claim census on quick path).
        include_dual_presence = bool(include_fp)

    dual_presence: DualPresenceSection | None = None
    if include_dual_presence:
        dual_presence = _collect_dual_presence_section(
            db_path=target,
            thr=thr,
            fp=fp,
            quick=quick,
            probes=probes,
        )

    attached: tuple[AttachedImportHealth, ...] = ()
    if include_imports:
        attached = tuple(_collect_attached_imports(target, thr=thr, now=ref))


    fleet_summary: FleetHealthSummary | None = None
    fleet_checks: list[HealthCheckResult] = []
    if include_fleet is None:
        include_fleet = bool(include_imports)
    if include_fleet:
        fleet_summary, fleet_checks = _collect_fleet_section(
            target,
            thr=thr,
            now=ref,
            quick=quick,
            include_imports=include_imports,
        )

    mounts: tuple[MountProbe, ...] = ()
    if probes:
        try:
            mounts = tuple(probe_mounts(thresholds=thr, db_path=db_path))
        except Exception as exc:  # noqa: BLE001 — best-effort health section
            log_swallowed_error(
                "health.collect.probes",
                exc,
                context={"db_path": str(target)},
            )
            mounts = ()

    checks = build_health_checks(
        inventory=inventory,
        scan_freshness=scan_freshness,
        stash=stash,
        adapters=adapters,
        fp=fp,
        attached_imports=attached,
        mounts=mounts,
        rollups=rollups,
        thresholds=thr,
        dual_presence=dual_presence,
    )
    if fleet_checks:
        checks = list(checks) + list(fleet_checks)
    elif fleet_summary is not None:
        checks = list(checks) + _fleet_summary_checks(fleet_summary)
    overall = compute_overall(checks)
    notes: list[str] = []
    if quick:
        notes.append("quick=true: audit chain + stash CTE skipped unless cached")
    if not probes:
        notes.append("mount probes disabled")
    if not include_fp:
        notes.append("FP section not collected")

    return EstateHealthReport(
        generated_at=generated_at,
        machine_id=machine_id,
        overall=overall,
        inventory=inventory,
        scan_freshness=tuple(scan_freshness),
        stash=stash,
        adapters=adapters,
        schedule=schedule,
        fp=fp,
        attached_imports=attached,
        mounts=mounts,
        rollups=rollups,
        checks=tuple(checks),
        dual_presence=dual_presence,
        fleet=fleet_summary,
        notes=tuple(notes),
        quick=quick,
    )


def _inventory_from_status(
    status: StatusReport,
    *,
    thr: HealthThresholds,
    now: datetime,
) -> InventoryIntegrity:
    inv = status.inventory
    audit = status.audit_chain
    roll = status.rollups
    counts_source: str = "unknown"
    rollup_refreshed_at: str | None = None
    rollup_age: float | None = None
    used_cache = False
    if roll is not None:
        used_cache = roll.used_cache
        rollup_refreshed_at = roll.refreshed_at
        rollup_age = age_hours(roll.refreshed_at, now=now)
        if roll.used_cache:
            counts_source = "rollup"
        elif roll.refreshed_at is not None:
            counts_source = "live"
        else:
            counts_source = "live"
    else:
        counts_source = "live"

    return InventoryIntegrity(
        permanodes=inv.permanodes,
        current_claims=inv.current_claims,
        scan_runs=inv.scan_runs,
        audit_entries=inv.audit_entries,
        machines=inv.machines,
        db_size_bytes=status.db.size_bytes,
        db_path=status.db.path,
        audit_ok=None if audit.skipped else audit.ok,
        audit_skipped=audit.skipped,
        audit_error=audit.error,
        audit_rows_checked=audit.rows_checked,
        counts_source=counts_source,  # type: ignore[arg-type]
        rollup_refreshed_at=rollup_refreshed_at,
        rollup_age_hours=rollup_age,
        rollup_used_cache=used_cache,
    )


def _rollups_from_status(
    status: StatusReport,
    *,
    now: datetime,
) -> HealthRollupInfo | None:
    roll = status.rollups
    if roll is None:
        return None
    return HealthRollupInfo(
        used_cache=roll.used_cache,
        refreshed_at=roll.refreshed_at,
        max_age_seconds=roll.max_age_seconds,
        age_hours=age_hours(roll.refreshed_at, now=now),
    )


def _stash_from_status(
    status: StatusReport,
    *,
    thr: HealthThresholds,
    now: datetime,
    quick: bool,
) -> StashHealth:
    s = status.stash
    if quick and s.in_flight_entries == 0 and s.oldest_ts_iso is None:
        return StashHealth(
            in_flight_entries=0,
            distinct_run_ids=0,
            oldest_ts_iso=None,
            newest_ts_iso=None,
            age_hours_oldest=None,
            cooling_off_days=thr.cooling_off_days,
            grace_hours=thr.stash_grace_hours,
            overdue=None,
            source="skipped",
            level="skipped",
        )
    age = age_hours(s.oldest_ts_iso, now=now)
    limit = thr.stash_overdue_hours
    overdue: bool | None
    if s.in_flight_entries == 0:
        overdue = False
        level: str = "ok"
    elif age is None:
        overdue = None
        level = "unknown"
    else:
        overdue = age > limit
        level = "fail" if overdue else "ok"
    return StashHealth(
        in_flight_entries=s.in_flight_entries,
        distinct_run_ids=s.distinct_run_ids,
        oldest_ts_iso=s.oldest_ts_iso,
        newest_ts_iso=s.newest_ts_iso,
        age_hours_oldest=age,
        cooling_off_days=thr.cooling_off_days,
        grace_hours=thr.stash_grace_hours,
        overdue=overdue,
        source="live",
        level=level,  # type: ignore[arg-type]
    )


def _adapters_from_status(
    status: StatusReport,
    *,
    thr: HealthThresholds,
    now: datetime,
) -> AdapterFreshness:
    def _one(action: str, run: object | None) -> AdapterRunHealth | None:
        if run is None:
            return AdapterRunHealth(
                action=action,
                timestamp=None,
                age_hours=None,
                policy_name=None,
                level="unknown",
            )
        ts = getattr(run, "timestamp", None)
        policy = getattr(run, "policy_name", None)
        age = age_hours(ts, now=now)
        return AdapterRunHealth(
            action=action,
            timestamp=ts,
            age_hours=age,
            policy_name=policy,
            level=adapter_run_level(age, thresholds=thr, present=ts is not None),
        )

    rep = _one("replicate_end", status.last_replicate)
    arch = _one("archive_end", status.last_archive)
    levels = [x.level for x in (rep, arch) if x is not None]
    return AdapterFreshness(
        replicate=rep,
        archive=arch,
        level=worst_level(levels) if levels else "unknown",
    )


def _collect_scan_freshness(
    db_path: Path,
    *,
    thr: HealthThresholds,
    now: datetime,
) -> list[RootScanFreshness]:
    """Latest finished scan_run per root_path (indexed; no claims scan)."""
    out: list[RootScanFreshness] = []
    try:
        con = connect(db_path, read_only=True, load_vec=False)
    except Exception as exc:  # noqa: BLE001 — best-effort health section
        log_swallowed_error(
            "health.collect.scan_freshness_connect",
            exc,
            context={"db_path": str(db_path)},
        )
        return out
    rows: list[tuple[object, ...]] = []
    unfinished: list[tuple[object, ...]] = []
    try:
        # Join keeps id/files/errors from a finished row at max finished_at.
        rows = list(
            con.execute(
                """
                SELECT s.root_path, s.id, s.finished_at, s.files_walked, s.errors
                FROM scan_runs s
                INNER JOIN (
                    SELECT root_path, MAX(finished_at) AS max_fin
                    FROM scan_runs
                    WHERE finished_at IS NOT NULL
                    GROUP BY root_path
                ) t
                  ON s.root_path = t.root_path
                 AND s.finished_at = t.max_fin
                WHERE s.finished_at IS NOT NULL
                ORDER BY s.root_path, s.id DESC
                """
            ).fetchall()
        )
        unfinished = list(
            con.execute(
                """
                SELECT root_path, started_at
                FROM scan_runs
                WHERE finished_at IS NULL
                ORDER BY started_at DESC
                """
            ).fetchall()
        )
    except sqlite3.Error as exc:
        log_swallowed_error(
            "health.collect.scan_freshness_sql",
            exc,
            context={"db_path": str(db_path)},
        )
        return out
    finally:
        try:
            con.close()
        except Exception as exc:  # noqa: BLE001 — best-effort health section
            log_swallowed_error(
                "health.collect.scan_freshness_close",
                exc,
                context={"db_path": str(db_path)},
            )

    unfinished_by_root: dict[str, str] = {}
    for uroot, started in unfinished:
        key = str(uroot)
        if key not in unfinished_by_root:
            unfinished_by_root[key] = str(started)

    by_root: dict[str, RootScanFreshness] = {}
    for row in rows:
        root = str(row[0])
        if root in by_root:
            continue  # first row is highest id (ORDER BY id DESC)
        finished_at = str(row[2]) if row[2] is not None else None
        age = age_hours(finished_at, now=now)
        tier, _ = classify_tier(root if root.endswith("/") else root + "/")
        tier_label: str | None = None if tier == "unknown" else tier
        level = root_scan_level(
            age, thresholds=thr, has_finished=finished_at is not None
        )
        unfinished_started = unfinished_by_root.get(root)
        by_root[root] = RootScanFreshness(
            root_path=root,
            tier=tier_label,
            scan_run_id=int(str(row[1])) if row[1] is not None else None,
            finished_at=finished_at,
            age_hours=age,
            level=level,
            files_walked=int(str(row[3] or 0)),
            errors=int(str(row[4] or 0)),
            unfinished=unfinished_started is not None,
            unfinished_started_at=unfinished_started,
        )

    out = sorted(by_root.values(), key=lambda r: r.root_path)
    for uroot, started in unfinished_by_root.items():
        if uroot in by_root:
            continue
        tier, _ = classify_tier(uroot if uroot.endswith("/") else uroot + "/")
        out.append(
            RootScanFreshness(
                root_path=uroot,
                tier=None if tier == "unknown" else tier,
                scan_run_id=None,
                finished_at=None,
                age_hours=None,
                level="fail",
                unfinished=True,
                unfinished_started_at=str(started),
            )
        )
    return out



def _collect_dual_presence_section(
    *,
    db_path: Path,
    thr: HealthThresholds,
    fp: FPSection,
    quick: bool,
    probes: bool,
) -> DualPresenceSection:
    """Bounded dual-presence sample for estate health (ADR-0020).

    Quick path: fixed relatives only (no claim table scan).
    Full/probes: optional SQL sample of DropboxStorage current claims LIMIT N.
    """
    try:
        from steward.infra.dual_presence import (
            collect_dual_presence_stats,
            collect_stats_from_fixed_rels,
            default_mount_root,
            default_store_root,
            ready_for_cloud_filter,
            sample_claim_paths,
        )
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("health.collect.dual_presence_import", exc, context={})
        return DualPresenceSection(
            present=False,
            level="unknown",
            notes=(f"dual_presence module unavailable: {exc!r}",),
        )

    store_root = fp.store_root or str(default_store_root())
    mount_root = fp.mount_root or str(default_mount_root())
    notes: list[str] = []
    limit = thr.dual_presence_sample_limit
    stats = None
    sample_source = "fixed_rels"

    if not quick or probes:
        try:
            con = connect(db_path, read_only=True, load_vec=False)
            try:
                paths = sample_claim_paths(con, limit=limit)
            finally:
                con.close()
            if paths:
                stats = collect_dual_presence_stats(
                    paths,
                    store_root=store_root,
                    mount_root=mount_root,
                    intent="observe",
                    limit=limit,
                )
                sample_source = "claims_sample"
            else:
                notes.append("no DropboxStorage claims for sample; using fixed rels")
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "health.collect.dual_presence_claims",
                exc,
                context={"db_path": str(db_path)},
            )
            notes.append(f"claim sample failed: {exc!r}")

    if stats is None:
        try:
            stats = collect_stats_from_fixed_rels(
                store_root=store_root,
                mount_root=mount_root,
                intent="observe",
            )
            sample_source = "fixed_rels"
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("health.collect.dual_presence_fixed", exc, context={})
            return DualPresenceSection(
                present=False,
                level="unknown",
                layout=fp.layout,
                store_root=store_root,
                mount_root=mount_root,
                notes=(f"dual_presence probe failed: {exc!r}",),
            )

    mount_present = True
    if fp.present and fp.layout in ("missing", "store_only"):
        mount_present = False
    elif fp.present and fp.cloud_retire_ready is False and fp.layout == "missing":
        mount_present = False
    ready = ready_for_cloud_filter(stats, mount_present=mount_present)
    ratio = stats.cloud_safe_sample_ratio()
    thr_ratio = thr.dual_presence_ratio_min
    if stats.counted == 0:
        level: HealthLevel = "unknown"
    elif ratio is not None and ratio < thr_ratio:
        level = "fail"
    elif stats.mount_error > 0 and stats.dual == 0:
        level = "warn"
    elif ready:
        level = "ok"
    else:
        level = "warn"

    notes.append(f"sample_source={sample_source}")
    return DualPresenceSection(
        present=True,
        counted=stats.counted,
        dual=stats.dual,
        store_only=stats.store_only,
        mount_only=stats.mount_only,
        missing_store=stats.missing_store,
        conflict_name_path=stats.conflict_name_path,
        outside_store_root=stats.outside_store_root,
        mount_error=stats.mount_error,
        unknown=stats.unknown,
        cloud_safe_sample_ratio=ratio,
        layout=fp.layout,
        ready_for_cloud_filter=ready,
        store_root=stats.store_root or store_root,
        mount_root=stats.mount_root or mount_root,
        sample_limit=stats.sample_limit or limit,
        truncated=stats.truncated,
        level=level,
        notes=tuple(notes),
    )


def _collect_fp_section(*, include_fp: bool) -> FPSection:
    if not include_fp:
        return FPSection(present=False, level="skipped")
    try:
        report = collect_fp_status(probe_name_divergence=False)
    except Exception as exc:  # noqa: BLE001 — best-effort health section
        log_swallowed_error("health.collect.fp", exc, context={})
        return FPSection(
            present=False,
            level="unknown",
            notes=(f"FP collect failed: {exc!r}",),
        )
    verdict = report.verdict
    if verdict is None:
        return FPSection(
            present=True,
            layout=None,
            cloud_retire_ready=None,
            local_reclaim_ready=None,
            mount_root=report.mount_root,
            store_root=report.store_root,
            level="unknown",
        )
    if verdict.cloud_retire_ready and not verdict.problems:
        level = "ok"
    elif verdict.problems or not verdict.cloud_retire_ready:
        level = "fail"
    elif verdict.warnings:
        level = "warn"
    else:
        level = "ok"
    level_fp: HealthLevel = level  # type: ignore[assignment]
    return FPSection(
        present=True,
        layout=verdict.layout,
        cloud_retire_ready=verdict.cloud_retire_ready,
        local_reclaim_ready=verdict.local_reclaim_ready,
        problems=verdict.problems,
        warnings=verdict.warnings,
        notes=verdict.notes,
        mount_root=report.mount_root,
        store_root=report.store_root,
        level=level_fp,
    )


def _collect_attached_imports(
    db_path: Path,
    *,
    thr: HealthThresholds,
    now: datetime,
) -> list[AttachedImportHealth]:
    out: list[AttachedImportHealth] = []
    try:
        from steward.infra.sync.imports_admin import list_imports

        rows = list_imports(db_path=db_path)
    except Exception as exc:  # noqa: BLE001 — best-effort health section
        log_swallowed_error(
            "health.collect.attached",
            exc,
            context={"db_path": str(db_path)},
        )
        return out
    for row in rows:
        import_age = age_hours(row.imported_at, now=now)
        chain_age = age_hours(row.chain_verified_at, now=now)
        level, message = attached_import_level(
            payload_exists=row.payload_exists,
            import_age_hours=import_age,
            chain_verified_at=row.chain_verified_at,
            chain_verify_age_hours=chain_age,
            thresholds=thr,
        )
        out.append(
            AttachedImportHealth(
                machine_id=row.machine_id,
                file_path=str(row.file_path),
                imported_at=row.imported_at,
                import_age_hours=import_age,
                chain_verified_at=row.chain_verified_at,
                chain_verify_age_hours=chain_age,
                payload_exists=row.payload_exists,
                audit_rows=row.audit_rows,
                level=level,
                message=message,
            )
        )
    return out


def _collect_schedule_health(*, quick: bool) -> ScheduleHealth:
    """List bundled templates + installed presence; reliability when not quick.

    ADR-0019: full path uses ``collect_schedule_reliability(probe=True)`` so
    last exit / overdue deepen the estate health schedule section. Quick path
    stays cheap (template install presence only; no launchctl print).
    """
    if not quick:
        try:
            # Soft import: schedule is monorepo-only (stripped from open-core).
            import importlib

            rel_mod = importlib.import_module("steward.infra.schedule.reliability")
            collect_schedule_reliability = getattr(rel_mod, "collect_schedule_reliability")
            jobs = collect_schedule_reliability(probe=True)
            items = [
                ScheduleTemplateHealth(
                    name=j.name,
                    label=j.label,
                    installed=j.installed,
                    level=j.level,
                    message=j.message
                    + (
                        f" overdue={j.overdue}"
                        if j.overdue is not None
                        else ""
                    ),
                )
                for j in jobs
            ]
            if not items:
                return ScheduleHealth(
                    available=True,
                    templates=(),
                    level="unknown",
                    message="no schedule templates bundled",
                )
            levels: list[HealthLevel] = [i.level for i in items]
            worst = worst_level(levels)
            overdue_n = sum(1 for j in jobs if j.overdue is True)
            msg = (
                f"{overdue_n} overdue; "
                if overdue_n
                else ""
            ) + (
                "one or more templates not ok"
                if worst in ("warn", "fail")
                else "schedules on cadence"
            )
            return ScheduleHealth(
                available=True,
                templates=tuple(items),
                level=worst,
                message=msg,
            )
        except Exception as exc:  # noqa: BLE001 — fall through to cheap path
            log_swallowed_error("health.collect.schedule_reliability", exc, context={})

    try:
        import importlib

        templates_mod = importlib.import_module("steward.infra.schedule.templates")
        list_templates = getattr(templates_mod, "list_templates")
    except Exception as exc:  # noqa: BLE001 — best-effort health section
        log_swallowed_error("health.collect.schedule_import", exc, context={})
        return ScheduleHealth(
            available=False,
            templates=(),
            level="unknown",
            message="schedule module not available",
        )
    try:
        templates = list_templates()
    except Exception as exc:  # noqa: BLE001 — best-effort health section
        log_swallowed_error("health.collect.schedule_list", exc, context={})
        return ScheduleHealth(
            available=False,
            templates=(),
            level="unknown",
            message=f"schedule list failed: {exc!r}",
        )
    items = []
    for tmpl in templates:
        installed = tmpl.installed_plist_path.is_file()
        items.append(
            ScheduleTemplateHealth(
                name=tmpl.name,
                label=tmpl.label,
                installed=installed,
                level="ok" if installed else "warn",
                message="installed" if installed else "plist not installed",
            )
        )
    if not items:
        level = "unknown"
        msg = "no schedule templates bundled"
    elif any(not t.installed for t in items):
        level = "warn"
        msg = "one or more launchd templates not installed"
    else:
        level = "ok"
        msg = "all bundled templates installed"
    level_sched: HealthLevel = level  # type: ignore[assignment]
    return ScheduleHealth(
        available=True,
        templates=tuple(items),
        level=level_sched,
        message=msg,
    )






def _fleet_summary_checks(fleet: FleetHealthSummary) -> list[HealthCheckResult]:
    """Named opt-in fail-on checks derived from FleetHealthSummary."""
    from steward.core.health.model import HealthCheckResult
    from steward.core.health.thresholds import (
        FAIL_ON_ATTACHED_MISSING,
        FAIL_ON_ENVELOPE_SLA,
        FAIL_ON_FLEET_CHAIN_STALE,
        FAIL_ON_FLEET_STALE_SCAN,
    )

    checks: list[HealthCheckResult] = []
    # fleet_stale_scan / chain: approximate from overall + stale ids
    if fleet.overall == "fail" and fleet.stale_machine_ids:
        checks.append(
            HealthCheckResult(
                name=FAIL_ON_FLEET_STALE_SCAN,
                level="fail",
                message=f"{len(fleet.stale_machine_ids)} fleet machine(s) not ok",
                details={"stale_machine_ids": list(fleet.stale_machine_ids)},
            )
        )
    else:
        checks.append(
            HealthCheckResult(
                name=FAIL_ON_FLEET_STALE_SCAN,
                level="ok" if fleet.overall in ("ok", "warn", "unknown") else fleet.overall,
                message="Fleet scan posture from matrix summary",
                details={"machine_count": fleet.machine_count, "overall": fleet.overall},
            )
        )
    # chain token: fail only when overall fail and missing payload (strong signal)
    if fleet.attached_missing_payload > 0:
        chain_level = "fail"
        chain_msg = f"{fleet.attached_missing_payload} attached payload(s) missing (chain fail)"
    else:
        chain_level = "ok"
        chain_msg = "No attached payload missing"
    checks.append(
        HealthCheckResult(
            name=FAIL_ON_FLEET_CHAIN_STALE,
            level=chain_level,  # type: ignore[arg-type]
            message=chain_msg,
            details={"attached_missing_payload": fleet.attached_missing_payload},
        )
    )
    checks.append(
        HealthCheckResult(
            name=FAIL_ON_ENVELOPE_SLA,
            level=fleet.envelope_sla_level,
            message=(
                "Envelope SLA fail"
                if fleet.envelope_sla_level == "fail"
                else "Envelope SLA "
                + fleet.envelope_sla_level
            ),
            details={
                "local_export_age_hours": fleet.local_export_age_hours,
                "attached_stale_count": fleet.attached_stale_count,
                "attached_missing_payload": fleet.attached_missing_payload,
            },
        )
    )
    if fleet.attached_missing_payload > 0:
        checks.append(
            HealthCheckResult(
                name=FAIL_ON_ATTACHED_MISSING,
                level="fail",
                message=f"{fleet.attached_missing_payload} attached payload file(s) missing",
                details={"count": fleet.attached_missing_payload},
            )
        )
    else:
        checks.append(
            HealthCheckResult(
                name=FAIL_ON_ATTACHED_MISSING,
                level="ok",
                message="No missing attached payloads",
                details={"attached_count": fleet.attached_count},
            )
        )
    return checks


def _collect_fleet_section(
    db_path: Path,
    *,
    thr: HealthThresholds,
    now: datetime,
    quick: bool,
    include_imports: bool,
) -> tuple[FleetHealthSummary | None, list[HealthCheckResult]]:
    """Best-effort ADR-0021 matrix summary + checks for estate composition."""
    try:
        from steward.core.fleet import FleetThresholds, fleet_section_from_matrix
        from steward.infra.db.settings import data_dir
        from steward.infra.fleet import collect_fleet_health

        fthr = FleetThresholds(
            scan_max_age_hours=thr.scan_max_age_hours,
            envelope_max_age_hours=192.0,
            attached_max_age_days=thr.attached_max_age_days,
            chain_verify_max_age_days=thr.attached_max_age_days,
        )
        matrix = collect_fleet_health(
            db_path=db_path,
            include_imports=include_imports,
            quick=quick,
            thresholds=fthr,
            data_dir=data_dir(),
            now=now,
        )
        section = fleet_section_from_matrix(matrix)
        sla = section["envelope_sla"]
        summary = FleetHealthSummary(
            overall=section["overall"],
            machine_count=int(section["machine_count"]),
            attached_count=int(section["attached_count"]),
            envelope_sla_level=sla["level"],
            local_export_age_hours=sla.get("local_export_age_hours"),
            attached_stale_count=int(sla.get("attached_stale_count") or 0),
            attached_missing_payload=int(sla.get("attached_missing_payload") or 0),
            stale_machine_ids=tuple(section.get("stale_machine_ids") or ()),
            notes=tuple(section.get("notes") or ()),
        )
        # Matrix checks already use ADR-0021 token names
        fleet_checks = list(matrix.checks)
        return summary, fleet_checks
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "health.collect.fleet",
            exc,
            context={"db_path": str(db_path)},
        )
        return None, []


def estate_health_to_dict(report: EstateHealthReport) -> dict[str, Any]:
    """JSON-stable serialization for CLI --json, MCP, dashboard."""
    return {
        "generated_at": report.generated_at,
        "machine_id": report.machine_id,
        "overall": report.overall,
        "quick": report.quick,
        "inventory": asdict(report.inventory),
        "scan_freshness": [asdict(r) for r in report.scan_freshness],
        "stash": asdict(report.stash),
        "adapters": asdict(report.adapters),
        "schedule": asdict(report.schedule) if report.schedule is not None else None,
        "fp": asdict(report.fp),
        "dual_presence": asdict(report.dual_presence) if report.dual_presence is not None else None,
        "fleet": asdict(report.fleet) if report.fleet is not None else None,
        "attached_imports": [asdict(a) for a in report.attached_imports],
        "mounts": [asdict(m) for m in report.mounts],
        "rollups": asdict(report.rollups) if report.rollups is not None else None,
        "checks": [asdict(c) for c in report.checks],
        "notes": list(report.notes),
    }


def estate_health_to_snapshot_dict(
    report: EstateHealthReport,
    *,
    compact: bool = True,
) -> dict[str, Any]:
    """Compact dict for JSONL sparklines (drop bulky lists)."""
    if not compact:
        return estate_health_to_dict(report)
    return {
        "generated_at": report.generated_at,
        "machine_id": report.machine_id,
        "overall": report.overall,
        "quick": report.quick,
        "inventory": {
            "permanodes": report.inventory.permanodes,
            "current_claims": report.inventory.current_claims,
            "counts_source": report.inventory.counts_source,
            "audit_ok": report.inventory.audit_ok,
            "audit_skipped": report.inventory.audit_skipped,
        },
        "scan_freshness": [
            {
                "root_path": r.root_path,
                "age_hours": r.age_hours,
                "level": r.level,
            }
            for r in report.scan_freshness
        ],
        "stash": {
            "in_flight_entries": report.stash.in_flight_entries,
            "age_hours_oldest": report.stash.age_hours_oldest,
            "level": report.stash.level,
        },
        "adapters": {
            "level": report.adapters.level,
            "replicate_age_hours": (
                report.adapters.replicate.age_hours if report.adapters.replicate else None
            ),
            "archive_age_hours": (
                report.adapters.archive.age_hours if report.adapters.archive else None
            ),
        },
        "fp": {
            "present": report.fp.present,
            "layout": report.fp.layout,
            "cloud_retire_ready": report.fp.cloud_retire_ready,
            "level": report.fp.level,
        },
        "dual_presence": (
            {
                "present": report.dual_presence.present,
                "counted": report.dual_presence.counted,
                "dual": report.dual_presence.dual,
                "store_only": report.dual_presence.store_only,
                "cloud_safe_sample_ratio": report.dual_presence.cloud_safe_sample_ratio,
                "ready_for_cloud_filter": report.dual_presence.ready_for_cloud_filter,
                "level": report.dual_presence.level,
            }
            if report.dual_presence is not None
            else None
        ),
        "fleet": (
            {
                "overall": report.fleet.overall,
                "machine_count": report.fleet.machine_count,
                "attached_count": report.fleet.attached_count,
                "envelope_sla_level": report.fleet.envelope_sla_level,
                "local_export_age_hours": report.fleet.local_export_age_hours,
                "attached_stale_count": report.fleet.attached_stale_count,
                "stale_machine_ids": list(report.fleet.stale_machine_ids),
            }
            if report.fleet is not None
            else None
        ),
        "mounts": [
            {
                "root": m.root,
                "tier": m.tier,
                "present": m.present,
                "free_bytes": m.free_bytes,
                "total_bytes": m.total_bytes,
                "sample_latency_ms": m.sample_latency_ms,
                "level": m.level,
            }
            for m in report.mounts
        ],
        "checks": [
            {"name": c.name, "level": c.level, "message": c.message} for c in report.checks
        ],
        "rollups": asdict(report.rollups) if report.rollups is not None else None,
    }


def run_health_check(
    report: EstateHealthReport,
    fail_on: frozenset[str] | set[str] | None = None,
    *,
    thresholds: HealthThresholds | None = None,
) -> dict[str, Any]:
    """Evaluate fail-on set; return ``{ok, failed, checks, report}``."""
    from steward.core.health.evaluate import evaluate_fail_on
    from steward.core.health.thresholds import DEFAULT_CHECK_FAIL_ON

    tokens = frozenset(fail_on) if fail_on is not None else DEFAULT_CHECK_FAIL_ON
    thr = thresholds or DEFAULT_THRESHOLDS
    failed = evaluate_fail_on(report, tokens, thresholds=thr)
    selected = [c for c in report.checks if c.name in tokens]
    return {
        "ok": len(failed) == 0,
        "failed": [asdict(c) for c in failed],
        "checks": [asdict(c) for c in selected],
        "report": estate_health_to_dict(report),
    }

__all__ = [
    "collect_estate_health",
    "estate_health_to_dict",
    "estate_health_to_snapshot_dict",
    "run_health_check",
]
