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
    # Capacity bands, added 2026-08-16. `_min` are the WARN floors, `_fail`
    # the FAIL floors; each pair must stay strictly tighter than its warn
    # counterpart.
    #
    # Why a fail band exists at all: `free_space_level` previously capped at
    # "warn", so a volume in the act of taking down the machine graded the
    # same as one merely getting full. On 2026-08-13 the Mac Pro boot volume
    # reached 100%, a CI runner crashed writing its own log, all three runners
    # went offline and Docker Desktop reset its VM. Three days after that
    # cleanup the same volume was back under 21 GiB free, burning ~20 GB/day.
    #
    # The two axes do different jobs and must not be conflated:
    #   * the ABSOLUTE floor means "this is too little space for anything to
    #     work, whatever the disk size" — so it stays small. Raising it to
    #     volume-sized numbers makes every small volume permanently red.
    #   * the RATIO floor does the proportional work and is what actually
    #     catches a large disk filling up.
    # They combine with OR: either kind of danger is danger.
    free_bytes_min: int = 25 * 1024**3  # 25 GiB — warn
    free_ratio_min: float = 0.08  # 8% — warn
    free_bytes_fail: int = 10 * 1024**3  # 10 GiB — fail
    free_ratio_fail: float = 0.03  # 3% — fail
    sample_latency_warn_ms: float = 2000.0
    unfinished_scan_warn_hours: float = 6.0
    # File Provider sync-loop detection (2026-08-16). error_generation is
    # fileproviderd's own failed-cycle counter: healthy domains sit at 0-2,
    # and the 46-day iCloud loop that silently filled the boot volume via
    # nsurlsessiond staging reached 628. The warn floor is set where a loop
    # is unambiguous but young — at ~1 generation/hour observed, 10 is hours
    # old, not weeks.
    fp_error_generation_warn: int = 10
    fp_error_generation_fail: int = 100
    # df-vs-reachable gap (same incident). DataVaults and APFS metadata make
    # a modest gap NORMAL — tens of GiB on a macOS boot volume — so floors
    # are absolute, not proportional: the vault baseline does not scale with
    # disk size the way content does. The incident gap was 441 GiB.
    unaccounted_warn_bytes: int = 64 * 1024**3
    unaccounted_fail_bytes: int = 160 * 1024**3
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
# Capacity tokens (ADR-0017 amendment, 2026-08-16). `mount_low` was deferred
# in v1, which meant a volume grading `fail` could not fail the gate: the
# estate check exited 0 while the boot disk was hours from taking the machine
# down. `mount_missing` is its sibling for roots that are absent entirely.
FAIL_ON_MOUNT_LOW = "mount_low"
FAIL_ON_MOUNT_MISSING = "mount_missing"
# Sync + accounting tokens (2026-08-16, same incident as the capacity
# bands): a File Provider domain looping on errors, and allocated space
# no traversal can reach. Both report `skipped` when their collector did
# not run, so they are safe in the default set — "not measured" never
# grades as "fine".
FAIL_ON_FP_SYNC_STUCK = "fp_sync_stuck"
FAIL_ON_UNACCOUNTED_SPACE = "unaccounted_space"
# Estate tokens (two-host estate). Graded only when an estate file exists, so
# default-on cannot affect a single-host install.
FAIL_ON_FOREIGN_ATTACH = "foreign_attach"
FAIL_ON_ESTATE_BINDING = "estate_binding"
# Multi-host estate: another host's capacity, graded on the primary from the
# health sidecar it pulled. Only emitted with an estate file, on the primary;
# opt-in like the fleet tokens (stale / missing sidecars grade warn / unknown).
FAIL_ON_REMOTE_CAPACITY = "remote_capacity"

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
        FAIL_ON_MOUNT_LOW,
        FAIL_ON_MOUNT_MISSING,
        FAIL_ON_FP_SYNC_STUCK,
        FAIL_ON_UNACCOUNTED_SPACE,
        FAIL_ON_FOREIGN_ATTACH,
        FAIL_ON_ESTATE_BINDING,
        FAIL_ON_REMOTE_CAPACITY,
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
        # Capacity is default-on, unlike the FP and fleet tokens above: every
        # host has disks, so it cannot false-red on a non-FP or single-machine
        # host. It is also safe despite `health check` defaulting to
        # --no-probes — with no probes the check reports `skipped`, never
        # `fail`, so the default set stays quiet until capacity is actually
        # measured.
        FAIL_ON_MOUNT_LOW,
        FAIL_ON_FP_SYNC_STUCK,
        FAIL_ON_UNACCOUNTED_SPACE,
        # An image attached on two hosts at once corrupts it; a DB bound to
        # another machine gets that machine's claims mutated from here.
        FAIL_ON_FOREIGN_ATTACH,
        FAIL_ON_ESTATE_BINDING,
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
    "FAIL_ON_ESTATE_BINDING",
    "FAIL_ON_FOREIGN_ATTACH",
    "FAIL_ON_ATTACHED_MISSING",
    "FAIL_ON_FP_NOT_READY",
    "FAIL_ON_FP_SYNC_STUCK",
    "FAIL_ON_MOUNT_LOW",
    "FAIL_ON_MOUNT_MISSING",
    "FAIL_ON_REMOTE_CAPACITY",
    "FAIL_ON_ROLLUP_STALE",
    "FAIL_ON_STALE_SCAN",
    "FAIL_ON_STASH_OVERDUE",
    "FAIL_ON_UNACCOUNTED_SPACE",
    "KNOWN_FAIL_ON_TOKENS",
    "HealthThresholds",
]
