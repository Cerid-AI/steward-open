# SPDX-License-Identifier: Apache-2.0

"""Grade another estate host's capacity from the health sidecar it publishes.

A client host writes ``health/latest.json`` (the compact estate-health
report); the primary pulls it into its inbox and grades it here. The
primary re-grades the client's mount probes against its own thresholds
rather than trusting the client's levels, so one set of numbers governs
the whole estate. Pure: the caller hands in the parsed sidecar.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from steward.core.health.evaluate import age_hours, free_space_level, worst_level
from steward.core.health.model import HealthCheckResult, HealthLevel, MountProbe
from steward.core.health.thresholds import (
    DEFAULT_THRESHOLDS,
    FAIL_ON_REMOTE_CAPACITY,
    HealthThresholds,
)

_LEVELS: dict[str, HealthLevel] = {
    "ok": "ok",
    "warn": "warn",
    "fail": "fail",
    "unknown": "unknown",
    "skipped": "skipped",
}


@dataclass(frozen=True, slots=True)
class RemoteHostCapacity:
    """One remote host's capacity verdict, as the primary sees it."""

    host_id: str
    level: HealthLevel
    message: str
    generated_at: str | None = None
    age_hours: float | None = None
    max_age_hours: float | None = None
    mounts: tuple[MountProbe, ...] = ()


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def sidecar_mounts(sidecar: Mapping[str, Any], *, thresholds: HealthThresholds) -> tuple[MountProbe, ...]:
    """Mount probes from a compact report, present ones re-graded with ``thresholds``."""
    raw = sidecar.get("mounts")
    if not isinstance(raw, list):
        return ()
    out: list[MountProbe] = []
    for entry in raw:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("root"), str):
            continue
        present = bool(entry.get("present"))
        free = _int_or_none(entry.get("free_bytes"))
        total = _int_or_none(entry.get("total_bytes"))
        reported = entry.get("level")
        if present:
            level: HealthLevel = free_space_level(free, total, thresholds=thresholds)
        else:
            level = _LEVELS.get(reported, "unknown") if isinstance(reported, str) else "unknown"
        tier = entry.get("tier")
        out.append(
            MountProbe(
                root=str(entry["root"]),
                tier=tier if isinstance(tier, str) else None,
                present=present,
                free_bytes=free,
                total_bytes=total,
                level=level,
            )
        )
    return tuple(out)


def grade_remote_capacity(
    host_id: str,
    sidecar: Mapping[str, Any] | None,
    *,
    max_age_hours: float,
    now: datetime | None = None,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
) -> RemoteHostCapacity:
    """Grade one host's pulled sidecar.

    No sidecar, or one without a readable ``generated_at``, is ``unknown``.
    A sidecar older than ``max_age_hours`` is ``warn`` whatever it says: its
    capacity numbers describe a disk as it was, not as it is.
    """
    if sidecar is None:
        return RemoteHostCapacity(
            host_id, "unknown", f"{host_id}: no health sidecar pulled", max_age_hours=max_age_hours
        )
    generated_at = sidecar.get("generated_at")
    generated = generated_at if isinstance(generated_at, str) else None
    age = age_hours(generated, now=now)
    if age is None:
        return RemoteHostCapacity(
            host_id,
            "unknown",
            f"{host_id}: health sidecar has no readable generated_at",
            generated_at=generated,
            max_age_hours=max_age_hours,
        )
    mounts = sidecar_mounts(sidecar, thresholds=thresholds)
    if age > max_age_hours:
        return RemoteHostCapacity(
            host_id,
            "warn",
            f"{host_id}: health sidecar is {age:.1f}h old (max {max_age_hours:g}h)",
            generated_at=generated,
            age_hours=age,
            max_age_hours=max_age_hours,
            mounts=mounts,
        )
    if not mounts:
        return RemoteHostCapacity(
            host_id,
            "unknown",
            f"{host_id}: sidecar carries no mount probes — capacity not measured",
            generated_at=generated,
            age_hours=age,
            max_age_hours=max_age_hours,
        )
    worst = worst_level(m.level for m in mounts)
    level: HealthLevel = "fail" if worst == "fail" else "warn" if worst in ("warn", "unknown") else "ok"
    flagged = [m.root for m in mounts if m.level not in ("ok", "skipped")]
    message = (
        f"{host_id}: {len(mounts)} mount(s) with healthy free space"
        if level == "ok"
        else f"{host_id}: {len(flagged)} mount(s) low, missing or unmeasurable"
    )
    return RemoteHostCapacity(
        host_id,
        level,
        message,
        generated_at=generated,
        age_hours=age,
        max_age_hours=max_age_hours,
        mounts=mounts,
    )


def check_remote_capacity(hosts: Sequence[RemoteHostCapacity]) -> HealthCheckResult:
    """The ``remote_capacity`` gate over every graded remote host."""
    if not hosts:
        return HealthCheckResult(
            name=FAIL_ON_REMOTE_CAPACITY,
            level="skipped",
            message="no remote hosts publish health",
            details={"hosts": {}},
        )
    level = worst_level(h.level for h in hosts)
    not_ok = [h for h in hosts if h.level != "ok"]
    message = (
        f"{len(hosts)} remote host(s) with healthy capacity" if not not_ok else "; ".join(h.message for h in not_ok)
    )
    details: dict[str, Any] = {
        "hosts": {
            h.host_id: {
                "level": h.level,
                "generated_at": h.generated_at,
                "age_hours": h.age_hours,
                "max_age_hours": h.max_age_hours,
                "roots": {m.root: m.level for m in h.mounts},
            }
            for h in hosts
        }
    }
    return HealthCheckResult(name=FAIL_ON_REMOTE_CAPACITY, level=level, message=message, details=details)


__all__ = [
    "RemoteHostCapacity",
    "check_remote_capacity",
    "grade_remote_capacity",
    "sidecar_mounts",
]
