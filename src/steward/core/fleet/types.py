# SPDX-License-Identifier: Apache-2.0

"""I/O-free fleet health matrix types (ADR-0021).

Reusable by open-core / steward-fs. Collectors live in
:mod:`steward.infra.fleet`. Shares :class:`HealthLevel` /
:class:`HealthCheckResult` shapes with ADR-0017.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from steward.core.health.model import HealthCheckResult, HealthLevel

MachineSource = Literal["local", "attached"]


@dataclass(frozen=True, slots=True)
class FleetThresholds:
    """Age thresholds for fleet matrix evaluation (ADR-0021 §3)."""

    scan_max_age_hours: float = 168.0  # 7d
    envelope_max_age_hours: float = 192.0  # 8d: weekly export + 1d grace
    attached_max_age_days: float = 30.0
    chain_verify_max_age_days: float = 30.0

    @property
    def attached_max_age_hours(self) -> float:
        return float(self.attached_max_age_days) * 24.0

    @property
    def chain_verify_max_age_hours(self) -> float:
        return float(self.chain_verify_max_age_days) * 24.0


DEFAULT_FLEET_THRESHOLDS = FleetThresholds()

# Named --fail-on tokens (fleet slice; opt-in on estate health check).
FAIL_ON_FLEET_STALE_SCAN = "fleet_stale_scan"
FAIL_ON_FLEET_CHAIN_STALE = "fleet_chain_stale"
FAIL_ON_ENVELOPE_SLA = "envelope_sla"
FAIL_ON_ATTACHED_MISSING = "attached_missing"

KNOWN_FLEET_FAIL_ON_TOKENS: frozenset[str] = frozenset(
    {
        FAIL_ON_FLEET_STALE_SCAN,
        FAIL_ON_FLEET_CHAIN_STALE,
        FAIL_ON_ENVELOPE_SLA,
        FAIL_ON_ATTACHED_MISSING,
    }
)

# Default for ``machines health --check`` when --fail-on omitted.
DEFAULT_FLEET_CHECK_FAIL_ON: frozenset[str] = frozenset(
    {
        FAIL_ON_FLEET_STALE_SCAN,
        FAIL_ON_FLEET_CHAIN_STALE,
        FAIL_ON_ENVELOPE_SLA,
        FAIL_ON_ATTACHED_MISSING,
    }
)


@dataclass(frozen=True, slots=True)
class MachineHealthRow:
    """One fleet matrix row (local or attached)."""

    machine_id: str
    source: MachineSource
    is_current: bool
    claim_count: int
    current_claim_count: int
    hostname: str | None = None
    last_scan_finished_at: str | None = None
    last_scan_root: str | None = None
    last_scan_errors: int | None = None
    scan_age_hours: float | None = None
    scan_level: HealthLevel = "unknown"
    chain_verified_at: str | None = None
    chain_age_hours: float | None = None
    chain_level: HealthLevel = "unknown"
    payload_exists: bool | None = None
    envelope_at: str | None = None
    envelope_age_hours: float | None = None
    envelope_level: HealthLevel = "unknown"
    audit_entry_count: int = 0
    schema_version: str | None = None
    payload_blake3: str | None = None
    level: HealthLevel = "unknown"


@dataclass(frozen=True, slots=True)
class EnvelopeSlaSummary:
    """Estate-level rollup of local export + attached import envelope ages."""

    local_export_at: str | None
    local_export_age_hours: float | None
    local_export_level: HealthLevel
    attached_count: int
    attached_stale_count: int
    attached_missing_payload: int
    attached_never_verified: int
    level: HealthLevel


@dataclass(frozen=True, slots=True)
class FleetHealthMatrix:
    """Multi-machine fleet health contract (ADR-0021)."""

    generated_at: str
    local_machine_id: str
    overall: HealthLevel
    thresholds: FleetThresholds
    rows: tuple[MachineHealthRow, ...]
    envelope_sla: EnvelopeSlaSummary
    checks: tuple[HealthCheckResult, ...]
    notes: tuple[str, ...] = ()
    quick: bool = True
    include_imports: bool = True


@dataclass(frozen=True, slots=True)
class FleetSection:
    """Compact fleet summary for EstateHealthReport composition (ADR-0017 §5)."""

    overall: HealthLevel
    machine_count: int
    attached_count: int
    envelope_sla: EnvelopeSlaSummary
    stale_machine_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "DEFAULT_FLEET_CHECK_FAIL_ON",
    "DEFAULT_FLEET_THRESHOLDS",
    "FAIL_ON_ATTACHED_MISSING",
    "FAIL_ON_ENVELOPE_SLA",
    "FAIL_ON_FLEET_CHAIN_STALE",
    "FAIL_ON_FLEET_STALE_SCAN",
    "KNOWN_FLEET_FAIL_ON_TOKENS",
    "EnvelopeSlaSummary",
    "FleetHealthMatrix",
    "FleetSection",
    "FleetThresholds",
    "MachineHealthRow",
    "MachineSource",
]
