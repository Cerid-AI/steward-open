# SPDX-License-Identifier: Apache-2.0

"""Default estate-health thresholds (ADR-0017) — policy numbers only."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HealthThresholds:
    """Age and capacity thresholds for estate-health evaluation.

    Defaults match ADR-0017 §3 / §6. All ages are wall-clock; collectors
    pass absolute ages into pure evaluators.
    """

    scan_max_age_hours: float = 168.0  # 7d
    stash_grace_hours: float = 24.0
    cooling_off_days: int = 7
    adapter_max_age_hours: float = 168.0  # 7d; soft unless fail-on
    rollup_max_age_hours: float = 24.0
    attached_max_age_days: float = 30.0
    free_bytes_min: int = 10 * 1024**3  # 10 GiB
    free_ratio_min: float = 0.05  # 5%
    sample_latency_warn_ms: float = 2000.0
    unfinished_scan_warn_hours: float = 6.0
    dual_presence_ratio_min: float = 0.5  # dual/(dual+store_only)
    dual_presence_sample_limit: int = 32

    @property
    def stash_overdue_hours(self) -> float:
        """Cooling-off window + grace, expressed in hours."""
        return float(self.cooling_off_days) * 24.0 + float(self.stash_grace_hours)

    @property
    def attached_max_age_hours(self) -> float:
        return float(self.attached_max_age_days) * 24.0


DEFAULT_THRESHOLDS = HealthThresholds()

# Named --fail-on tokens (v1).
FAIL_ON_STALE_SCAN = "stale_scan"
FAIL_ON_BROKEN_AUDIT = "broken_audit"
FAIL_ON_STASH_OVERDUE = "stash_overdue"
FAIL_ON_FP_NOT_READY = "fp_not_ready"
FAIL_ON_ROLLUP_STALE = "rollup_stale"
FAIL_ON_DUAL_PRESENCE_POOR = "dual_presence_poor"
# Fleet tokens (ADR-0021) — opt-in on estate health check
FAIL_ON_FLEET_STALE_SCAN = "fleet_stale_scan"
FAIL_ON_FLEET_CHAIN_STALE = "fleet_chain_stale"
FAIL_ON_ENVELOPE_SLA = "envelope_sla"
FAIL_ON_ATTACHED_MISSING = "attached_missing"

KNOWN_FAIL_ON_TOKENS: frozenset[str] = frozenset(
    {
        FAIL_ON_STALE_SCAN,
        FAIL_ON_BROKEN_AUDIT,
        FAIL_ON_STASH_OVERDUE,
        FAIL_ON_FP_NOT_READY,
        FAIL_ON_ROLLUP_STALE,
        FAIL_ON_DUAL_PRESENCE_POOR,
        FAIL_ON_FLEET_STALE_SCAN,
        FAIL_ON_FLEET_CHAIN_STALE,
        FAIL_ON_ENVELOPE_SLA,
        FAIL_ON_ATTACHED_MISSING,
    }
)

# Default for ``steward health check`` when --fail-on omitted.
# Local inventory integrity only. Opt-in (explicit --fail-on):
#   fp_not_ready, dual_presence_poor (ADR-0020), fleet_* / envelope_sla /
#   attached_missing (ADR-0021) — avoid false-red on non-FP / single-machine hosts.
DEFAULT_CHECK_FAIL_ON: frozenset[str] = frozenset(
    {
        FAIL_ON_STALE_SCAN,
        FAIL_ON_BROKEN_AUDIT,
        FAIL_ON_STASH_OVERDUE,
        FAIL_ON_ROLLUP_STALE,
    }
)

__all__ = [
    "DEFAULT_CHECK_FAIL_ON",
    "DEFAULT_THRESHOLDS",
    "FAIL_ON_BROKEN_AUDIT",
    "FAIL_ON_DUAL_PRESENCE_POOR",
    "FAIL_ON_FLEET_STALE_SCAN",
    "FAIL_ON_FLEET_CHAIN_STALE",
    "FAIL_ON_ENVELOPE_SLA",
    "FAIL_ON_ATTACHED_MISSING",
    "FAIL_ON_FP_NOT_READY",
    "FAIL_ON_ROLLUP_STALE",
    "FAIL_ON_STALE_SCAN",
    "FAIL_ON_STASH_OVERDUE",
    "KNOWN_FAIL_ON_TOKENS",
    "HealthThresholds",
]
