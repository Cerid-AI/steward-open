# SPDX-License-Identifier: Apache-2.0

"""I/O-free estate-health types (ADR-0017).

Composable contract for CLI / MCP / dashboard. Does **not** replace
:class:`StatusReport` or :class:`FPHealthVerdict` — infra collectors
copy section data into these pure shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

HealthLevel = Literal["ok", "warn", "fail", "unknown", "skipped"]

HealthCheckName = Literal[
    "stale_scan",
    "broken_audit",
    "stash_overdue",
    "fp_not_ready",
    "rollup_stale",
    "adapter_stale",
    "attached_stale",
    "mount_low",
    "mount_missing",
    "schedule_missing",
    "dual_presence_poor",
]


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    """Named gate unit; maps 1:1 to ``--fail-on`` tokens where applicable."""

    name: str
    level: HealthLevel
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InventoryIntegrity:
    """DB-open + counts + audit-chain posture (no claim table walk)."""

    permanodes: int
    current_claims: int
    scan_runs: int
    audit_entries: int
    machines: int
    db_size_bytes: int = 0
    db_path: str | None = None
    audit_ok: bool | None = None
    audit_skipped: bool = False
    audit_error: str | None = None
    audit_rows_checked: int = 0
    counts_source: Literal["live", "rollup", "unknown"] = "unknown"
    rollup_refreshed_at: str | None = None
    rollup_age_hours: float | None = None
    rollup_used_cache: bool = False


@dataclass(frozen=True, slots=True)
class RootScanFreshness:
    """Latest finished scan per root_path (+ optional tier label)."""

    root_path: str
    tier: str | None
    scan_run_id: int | None
    finished_at: str | None
    age_hours: float | None
    level: HealthLevel
    files_walked: int = 0
    errors: int = 0
    unfinished: bool = False
    unfinished_started_at: str | None = None


@dataclass(frozen=True, slots=True)
class StashHealth:
    """In-flight stash backlog posture."""

    in_flight_entries: int
    distinct_run_ids: int
    oldest_ts_iso: str | None
    newest_ts_iso: str | None
    age_hours_oldest: float | None
    cooling_off_days: int
    grace_hours: float
    overdue: bool | None
    source: Literal["live", "cache", "skipped"] = "skipped"
    level: HealthLevel = "unknown"


@dataclass(frozen=True, slots=True)
class AdapterRunHealth:
    """One adapter end-row (replicate / archive)."""

    action: str
    timestamp: str | None
    age_hours: float | None
    policy_name: str | None
    level: HealthLevel


@dataclass(frozen=True, slots=True)
class AdapterFreshness:
    """Latest replicate_end / archive_end posture."""

    replicate: AdapterRunHealth | None
    archive: AdapterRunHealth | None
    level: HealthLevel = "unknown"


@dataclass(frozen=True, slots=True)
class ScheduleTemplateHealth:
    name: str
    label: str
    installed: bool
    level: HealthLevel
    message: str = ""


@dataclass(frozen=True, slots=True)
class ScheduleHealth:
    """macOS launchd template presence (cheap path; optional probe later)."""

    available: bool
    templates: tuple[ScheduleTemplateHealth, ...] = ()
    level: HealthLevel = "unknown"
    message: str = ""


@dataclass(frozen=True, slots=True)
class FPSection:
    """FP layout verdict projection (no dual_samples payload)."""

    present: bool
    layout: str | None = None
    cloud_retire_ready: bool | None = None
    local_reclaim_ready: bool | None = None
    problems: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    mount_root: str | None = None
    store_root: str | None = None
    level: HealthLevel = "unknown"


@dataclass(frozen=True, slots=True)
class AttachedImportHealth:
    machine_id: str
    file_path: str
    imported_at: str | None
    import_age_hours: float | None
    chain_verified_at: str | None
    chain_verify_age_hours: float | None
    payload_exists: bool
    audit_rows: int = 0
    level: HealthLevel = "unknown"
    message: str = ""


@dataclass(frozen=True, slots=True)
class MountProbe:
    """Live volume / tier root probe (capacity + sample latency)."""

    root: str
    tier: str | None
    present: bool
    free_bytes: int | None = None
    total_bytes: int | None = None
    sample_latency_ms: float | None = None
    error: str | None = None
    level: HealthLevel = "unknown"
    message: str = ""


@dataclass(frozen=True, slots=True)
class HealthRollupInfo:
    """Whether inventory counts came from meta rollup cache."""

    used_cache: bool
    refreshed_at: str | None
    max_age_seconds: int | None = None
    age_hours: float | None = None




@dataclass(frozen=True, slots=True)
class DualPresenceSection:
    """Bounded dual-presence sample on estate health (ADR-0020).

    Compact counts only — never full per-path lists. ``None`` on the
    parent report means the section was not collected.
    """

    present: bool
    counted: int = 0
    dual: int = 0
    store_only: int = 0
    mount_only: int = 0
    missing_store: int = 0
    conflict_name_path: int = 0
    outside_store_root: int = 0
    mount_error: int = 0
    unknown: int = 0
    cloud_safe_sample_ratio: float | None = None
    layout: str | None = None
    ready_for_cloud_filter: bool = False
    store_root: str | None = None
    mount_root: str | None = None
    sample_limit: int | None = None
    truncated: bool = False
    level: HealthLevel = "unknown"
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FleetHealthSummary:
    """Compact fleet rollup on estate health (ADR-0021; full matrix in core.fleet)."""

    overall: HealthLevel
    machine_count: int
    attached_count: int
    envelope_sla_level: HealthLevel
    local_export_age_hours: float | None = None
    attached_stale_count: int = 0
    attached_missing_payload: int = 0
    stale_machine_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class EstateHealthReport:
    """Composite estate-health contract (ADR-0017)."""

    generated_at: str
    machine_id: str
    overall: HealthLevel
    inventory: InventoryIntegrity
    scan_freshness: tuple[RootScanFreshness, ...]
    stash: StashHealth
    adapters: AdapterFreshness
    schedule: ScheduleHealth | None
    fp: FPSection
    attached_imports: tuple[AttachedImportHealth, ...]
    mounts: tuple[MountProbe, ...]
    rollups: HealthRollupInfo | None
    checks: tuple[HealthCheckResult, ...]
    dual_presence: DualPresenceSection | None = None
    fleet: FleetHealthSummary | None = None
    notes: tuple[str, ...] = ()
    quick: bool = True


__all__ = [
    "AdapterFreshness",
    "AdapterRunHealth",
    "AttachedImportHealth",
    "DualPresenceSection",
    "EstateHealthReport",
    "FleetHealthSummary",
    "FPSection",
    "HealthCheckName",
    "HealthCheckResult",
    "HealthLevel",
    "HealthRollupInfo",
    "InventoryIntegrity",
    "MountProbe",
    "RootScanFreshness",
    "ScheduleHealth",
    "ScheduleTemplateHealth",
    "StashHealth",
]
