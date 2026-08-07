# SPDX-License-Identifier: Apache-2.0

"""Pure fleet SLA scoring and ``--fail-on`` evaluation (ADR-0021).

No SQLite, filesystem, or network. Unit-testable without infra.
Reuses :func:`age_hours` / :func:`level_for_age` / :func:`worst_level`
from :mod:`steward.core.health.evaluate`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from steward.core.fleet.types import (
    DEFAULT_FLEET_THRESHOLDS,
    FAIL_ON_ATTACHED_MISSING,
    FAIL_ON_ENVELOPE_SLA,
    FAIL_ON_FLEET_CHAIN_STALE,
    FAIL_ON_FLEET_STALE_SCAN,
    KNOWN_FLEET_FAIL_ON_TOKENS,
    EnvelopeSlaSummary,
    FleetHealthMatrix,
    FleetThresholds,
    MachineHealthRow,
)
from steward.core.health.evaluate import age_hours, level_for_age, worst_level
from steward.core.health.model import HealthCheckResult, HealthLevel


def scan_level_for_row(
    age: float | None,
    *,
    thresholds: FleetThresholds = DEFAULT_FLEET_THRESHOLDS,
    has_finished: bool,
) -> HealthLevel:
    """Level for one machine's latest finished scan age."""
    if not has_finished or age is None:
        return "fail"
    return level_for_age(age, thresholds.scan_max_age_hours, missing_level="fail")


def envelope_level_for_local(
    age: float | None,
    *,
    thresholds: FleetThresholds = DEFAULT_FLEET_THRESHOLDS,
) -> HealthLevel:
    """Local export envelope: missing → warn (opt-in fail-on); stale → fail."""
    if age is None:
        # Single-machine installs without weekly export should not hard-fail
        # the matrix overall unless envelope_sla is in fail-on (check builds
        # fail only when level==fail). Use warn so overall can stay ok.
        return "warn"
    return level_for_age(age, thresholds.envelope_max_age_hours, missing_level="warn")


def envelope_level_for_attached(
    age: float | None,
    *,
    thresholds: FleetThresholds = DEFAULT_FLEET_THRESHOLDS,
    payload_exists: bool,
) -> HealthLevel:
    """Attached import envelope: missing payload → fail; stale age → fail."""
    if not payload_exists:
        return "fail"
    if age is None:
        return "fail"
    return level_for_age(age, thresholds.attached_max_age_hours, missing_level="fail")


def chain_level_for_local(
    *,
    quick: bool,
    audit_ok: bool | None = None,
    audit_skipped: bool = False,
) -> HealthLevel:
    """Local chain: quick → unknown; full verify ok/fail."""
    if quick or audit_skipped:
        return "unknown"
    if audit_ok is True:
        return "ok"
    if audit_ok is False:
        return "fail"
    return "unknown"


def chain_level_for_attached(
    *,
    payload_exists: bool,
    chain_verified_at: str | None,
    chain_age_hours: float | None,
    thresholds: FleetThresholds = DEFAULT_FLEET_THRESHOLDS,
) -> HealthLevel:
    """Attached: missing payload → fail; never verified → warn; stale → warn."""
    if not payload_exists:
        return "fail"
    if chain_verified_at is None:
        return "warn"
    if (
        chain_age_hours is not None
        and chain_age_hours > thresholds.chain_verify_max_age_hours
    ):
        return "warn"
    return "ok"


def row_rollup_level(row: MachineHealthRow) -> HealthLevel:
    """Worst of scan / chain / envelope for one row."""
    return worst_level((row.scan_level, row.chain_level, row.envelope_level))


def build_envelope_sla(
    rows: Sequence[MachineHealthRow],
) -> EnvelopeSlaSummary:
    """Roll up local export + attached envelope signals from matrix rows."""
    local = next((r for r in rows if r.source == "local" and r.is_current), None)
    if local is None:
        local = next((r for r in rows if r.source == "local"), None)
    attached = [r for r in rows if r.source == "attached"]
    local_export_at = local.envelope_at if local is not None else None
    local_export_age = local.envelope_age_hours if local is not None else None
    local_export_level: HealthLevel = (
        local.envelope_level if local is not None else "unknown"
    )
    stale = sum(1 for r in attached if r.envelope_level in ("warn", "fail"))
    missing = sum(1 for r in attached if r.payload_exists is False)
    never_verified = sum(
        1
        for r in attached
        if r.payload_exists is not False and r.chain_verified_at is None
    )
    levels: list[HealthLevel] = [local_export_level]
    levels.extend(r.envelope_level for r in attached)
    # Missing payload also contributes fail via envelope_level, but include
    # explicit fail if any attached payload is gone.
    if missing:
        levels.append("fail")
    return EnvelopeSlaSummary(
        local_export_at=local_export_at,
        local_export_age_hours=local_export_age,
        local_export_level=local_export_level,
        attached_count=len(attached),
        attached_stale_count=stale,
        attached_missing_payload=missing,
        attached_never_verified=never_verified,
        level=worst_level(levels),
    )


def build_fleet_checks(
    matrix_rows: Sequence[MachineHealthRow],
    envelope_sla: EnvelopeSlaSummary,
    *,
    thresholds: FleetThresholds = DEFAULT_FLEET_THRESHOLDS,
) -> list[HealthCheckResult]:
    """Build named fail-on targets for the fleet matrix."""
    checks: list[HealthCheckResult] = []

    stale_scan = [r for r in matrix_rows if r.scan_level == "fail"]
    if stale_scan:
        checks.append(
            HealthCheckResult(
                name=FAIL_ON_FLEET_STALE_SCAN,
                level="fail",
                message=f"{len(stale_scan)} machine(s) with stale or missing finished scan",
                details={
                    "machine_ids": [r.machine_id for r in stale_scan],
                    "max_age_hours": thresholds.scan_max_age_hours,
                },
            )
        )
    else:
        checks.append(
            HealthCheckResult(
                name=FAIL_ON_FLEET_STALE_SCAN,
                level="ok",
                message="All fleet machines have fresh finished scans",
                details={
                    "machines": len(matrix_rows),
                    "max_age_hours": thresholds.scan_max_age_hours,
                },
            )
        )

    chain_fail = [r for r in matrix_rows if r.chain_level == "fail"]
    chain_warn = [r for r in matrix_rows if r.chain_level == "warn"]
    if chain_fail:
        checks.append(
            HealthCheckResult(
                name=FAIL_ON_FLEET_CHAIN_STALE,
                level="fail",
                message=f"{len(chain_fail)} machine(s) with failing chain signal",
                details={"machine_ids": [r.machine_id for r in chain_fail]},
            )
        )
    elif chain_warn:
        checks.append(
            HealthCheckResult(
                name=FAIL_ON_FLEET_CHAIN_STALE,
                level="warn",
                message=f"{len(chain_warn)} machine(s) with chain warn (never verified / stale)",
                details={"machine_ids": [r.machine_id for r in chain_warn]},
            )
        )
    else:
        # All ok or unknown (quick local) — ok when no hard fails
        unknown_n = sum(1 for r in matrix_rows if r.chain_level == "unknown")
        checks.append(
            HealthCheckResult(
                name=FAIL_ON_FLEET_CHAIN_STALE,
                level="ok" if unknown_n == 0 or unknown_n < len(matrix_rows) else "unknown",
                message=(
                    "Fleet chain signals ok"
                    if unknown_n == 0
                    else f"{unknown_n} machine(s) chain unknown (quick path)"
                ),
                details={"unknown": unknown_n, "machines": len(matrix_rows)},
            )
        )

    if envelope_sla.level == "fail":
        checks.append(
            HealthCheckResult(
                name=FAIL_ON_ENVELOPE_SLA,
                level="fail",
                message="Envelope sync SLA failed (local export or attached import)",
                details={
                    "local_export_level": envelope_sla.local_export_level,
                    "local_export_age_hours": envelope_sla.local_export_age_hours,
                    "attached_stale_count": envelope_sla.attached_stale_count,
                    "attached_missing_payload": envelope_sla.attached_missing_payload,
                    "envelope_max_age_hours": thresholds.envelope_max_age_hours,
                    "attached_max_age_days": thresholds.attached_max_age_days,
                },
            )
        )
    elif envelope_sla.level == "warn":
        checks.append(
            HealthCheckResult(
                name=FAIL_ON_ENVELOPE_SLA,
                level="warn",
                message="Envelope sync SLA warning (e.g. no local export yet)",
                details={
                    "local_export_level": envelope_sla.local_export_level,
                    "attached_stale_count": envelope_sla.attached_stale_count,
                },
            )
        )
    else:
        checks.append(
            HealthCheckResult(
                name=FAIL_ON_ENVELOPE_SLA,
                level=envelope_sla.level if envelope_sla.level != "skipped" else "ok",
                message="Envelope sync SLA within thresholds",
                details={
                    "local_export_age_hours": envelope_sla.local_export_age_hours,
                    "attached_count": envelope_sla.attached_count,
                },
            )
        )

    missing = [r for r in matrix_rows if r.source == "attached" and r.payload_exists is False]
    if missing:
        checks.append(
            HealthCheckResult(
                name=FAIL_ON_ATTACHED_MISSING,
                level="fail",
                message=f"{len(missing)} attached payload file(s) missing",
                details={"machine_ids": [r.machine_id for r in missing]},
            )
        )
    else:
        attached_n = sum(1 for r in matrix_rows if r.source == "attached")
        checks.append(
            HealthCheckResult(
                name=FAIL_ON_ATTACHED_MISSING,
                level="ok",
                message=(
                    "All attached payloads present"
                    if attached_n
                    else "No attached inventories"
                ),
                details={"attached_count": attached_n},
            )
        )

    return checks


def compute_fleet_overall(
    rows: Sequence[MachineHealthRow],
    checks: Sequence[HealthCheckResult],
) -> HealthLevel:
    """Overall = worst of row rollups and named checks."""
    levels: list[HealthLevel] = [r.level for r in rows]
    levels.extend(c.level for c in checks)
    return worst_level(levels) if levels else "unknown"


def evaluate_fleet_fail_on(
    matrix: FleetHealthMatrix,
    fail_on: frozenset[str] | set[str] | Sequence[str],
    *,
    thresholds: FleetThresholds | None = None,
) -> list[HealthCheckResult]:
    """Return checks among ``fail_on`` that are at level ``fail``."""
    tokens = frozenset(fail_on)
    thr = thresholds or matrix.thresholds
    checks: Sequence[HealthCheckResult] = matrix.checks
    if not checks:
        checks = build_fleet_checks(matrix.rows, matrix.envelope_sla, thresholds=thr)
    return [c for c in checks if c.name in tokens and c.level == "fail"]


def validate_fleet_fail_on_tokens(tokens: Iterable[str]) -> list[str]:
    """Return unknown fleet fail-on token names (empty if all known)."""
    return sorted({t for t in tokens if t not in KNOWN_FLEET_FAIL_ON_TOKENS})


def age_hours_from_iso(
    iso_ts: str | None,
    *,
    now: datetime | None = None,
) -> float | None:
    """Alias to shared :func:`age_hours` for fleet callers."""
    return age_hours(iso_ts, now=now)


def stale_machine_ids(rows: Sequence[MachineHealthRow]) -> list[str]:
    """Machine ids whose row rollup is warn or fail (compact estate section)."""
    return [r.machine_id for r in rows if r.level in ("warn", "fail")]


def fleet_section_from_matrix(matrix: FleetHealthMatrix) -> dict[str, Any]:
    """Compact dict for EstateHealthReport.fleet (no full per-machine tables)."""
    from steward.core.fleet.types import FleetSection

    attached_n = sum(1 for r in matrix.rows if r.source == "attached")
    section = FleetSection(
        overall=matrix.overall,
        machine_count=len(matrix.rows),
        attached_count=attached_n,
        envelope_sla=matrix.envelope_sla,
        stale_machine_ids=tuple(stale_machine_ids(matrix.rows)),
        notes=matrix.notes,
    )
    return {
        "overall": section.overall,
        "machine_count": section.machine_count,
        "attached_count": section.attached_count,
        "envelope_sla": {
            "local_export_at": section.envelope_sla.local_export_at,
            "local_export_age_hours": section.envelope_sla.local_export_age_hours,
            "local_export_level": section.envelope_sla.local_export_level,
            "attached_count": section.envelope_sla.attached_count,
            "attached_stale_count": section.envelope_sla.attached_stale_count,
            "attached_missing_payload": section.envelope_sla.attached_missing_payload,
            "attached_never_verified": section.envelope_sla.attached_never_verified,
            "level": section.envelope_sla.level,
        },
        "stale_machine_ids": list(section.stale_machine_ids),
        "notes": list(section.notes),
    }


__all__ = [
    "age_hours_from_iso",
    "build_envelope_sla",
    "build_fleet_checks",
    "chain_level_for_attached",
    "chain_level_for_local",
    "compute_fleet_overall",
    "envelope_level_for_attached",
    "envelope_level_for_local",
    "evaluate_fleet_fail_on",
    "fleet_section_from_matrix",
    "row_rollup_level",
    "scan_level_for_row",
    "stale_machine_ids",
    "validate_fleet_fail_on_tokens",
]
