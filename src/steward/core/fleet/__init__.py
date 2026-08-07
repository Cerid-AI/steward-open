# SPDX-License-Identifier: Apache-2.0

"""Fleet health matrix pure types and evaluation (ADR-0021).

Portable open-core surface: no SQLite / FS. Collectors live in
:mod:`steward.infra.fleet`.
"""

from __future__ import annotations

from steward.core.fleet.evaluate import (
    age_hours_from_iso,
    build_envelope_sla,
    build_fleet_checks,
    chain_level_for_attached,
    chain_level_for_local,
    compute_fleet_overall,
    envelope_level_for_attached,
    envelope_level_for_local,
    evaluate_fleet_fail_on,
    fleet_section_from_matrix,
    row_rollup_level,
    scan_level_for_row,
    stale_machine_ids,
    validate_fleet_fail_on_tokens,
)
from steward.core.fleet.types import (
    DEFAULT_FLEET_CHECK_FAIL_ON,
    DEFAULT_FLEET_THRESHOLDS,
    FAIL_ON_ATTACHED_MISSING,
    FAIL_ON_ENVELOPE_SLA,
    FAIL_ON_FLEET_CHAIN_STALE,
    FAIL_ON_FLEET_STALE_SCAN,
    KNOWN_FLEET_FAIL_ON_TOKENS,
    EnvelopeSlaSummary,
    FleetHealthMatrix,
    FleetSection,
    FleetThresholds,
    MachineHealthRow,
    MachineSource,
)

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
