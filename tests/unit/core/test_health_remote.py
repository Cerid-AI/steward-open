# SPDX-License-Identifier: Apache-2.0

"""``remote_capacity``: another host's capacity graded from its pulled health sidecar."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from steward.core.health import DEFAULT_CHECK_FAIL_ON, KNOWN_FAIL_ON_TOKENS
from steward.core.health.remote import (
    RemoteHostCapacity,
    check_remote_capacity,
    grade_remote_capacity,
    sidecar_mounts,
)
from steward.core.health.thresholds import DEFAULT_THRESHOLDS

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
GiB = 1024**3


def _mount(root: str, *, free: int, total: int = 1000 * GiB, present: bool = True, level: str = "ok") -> dict[str, Any]:
    return {
        "root": root,
        "tier": "L2",
        "present": present,
        "free_bytes": free if present else None,
        "total_bytes": total if present else None,
        "sample_latency_ms": 1.0,
        "level": level,
    }


def _sidecar(*mounts: dict[str, Any], age_hours: float = 2.0) -> dict[str, Any]:
    return {
        "generated_at": (NOW - timedelta(hours=age_hours)).isoformat(timespec="seconds"),
        "machine_id": "00000000-0000-4000-8000-000000000002",
        "overall": "ok",
        "mounts": list(mounts),
    }


def _grade(sidecar: dict[str, Any] | None, *, max_age_hours: float = 36.0) -> RemoteHostCapacity:
    return grade_remote_capacity("mac-pro", sidecar, max_age_hours=max_age_hours, now=NOW)


def test_healthy_sidecar_is_ok() -> None:
    graded = _grade(_sidecar(_mount("/", free=400 * GiB), _mount("/Volumes/Level 2", free=300 * GiB)))
    assert graded.level == "ok"
    assert graded.age_hours == pytest.approx(2.0)
    assert {m.root for m in graded.mounts} == {"/", "/Volumes/Level 2"}


def test_critically_low_mount_fails() -> None:
    graded = _grade(_sidecar(_mount("/", free=400 * GiB), _mount("/Volumes/Level 2", free=5 * GiB)))
    assert graded.level == "fail"
    assert "1 mount(s)" in graded.message


def test_low_mount_warns() -> None:
    assert _grade(_sidecar(_mount("/", free=20 * GiB, total=200 * GiB))).level == "warn"


def test_present_mounts_are_regraded_with_the_primarys_thresholds() -> None:
    """The client said ``ok`` about a disk that is nearly full; the primary does not take its word."""
    graded = _grade(_sidecar(_mount("/", free=5 * GiB, level="ok")))
    assert graded.mounts[0].level == "fail"
    assert graded.level == "fail"


def test_absent_mount_keeps_the_clients_level() -> None:
    mounts = sidecar_mounts(
        _sidecar(_mount("/Volumes/Level 1", free=0, present=False, level="fail")), thresholds=DEFAULT_THRESHOLDS
    )
    assert mounts[0].present is False and mounts[0].level == "fail"
    assert _grade(_sidecar(_mount("/Volumes/Level 1", free=0, present=False, level="warn"))).level == "warn"


def test_stale_sidecar_warns_even_when_its_numbers_are_healthy() -> None:
    graded = _grade(_sidecar(_mount("/", free=400 * GiB), age_hours=40.0), max_age_hours=36.0)
    assert graded.level == "warn"
    assert "40.0h old" in graded.message


def test_stale_sidecar_does_not_fail_on_old_numbers() -> None:
    assert _grade(_sidecar(_mount("/", free=1 * GiB), age_hours=72.0)).level == "warn"


def test_missing_sidecar_is_unknown() -> None:
    graded = _grade(None)
    assert graded.level == "unknown"
    assert "no health sidecar" in graded.message


def test_unreadable_generated_at_is_unknown() -> None:
    assert _grade({"mounts": [_mount("/", free=400 * GiB)]}).level == "unknown"
    assert _grade({}).level == "unknown"


def test_sidecar_without_probes_is_unknown() -> None:
    graded = _grade(_sidecar())
    assert graded.level == "unknown"
    assert "not measured" in graded.message


def test_malformed_mount_entries_are_skipped() -> None:
    sidecar = _sidecar(_mount("/", free=400 * GiB))
    sidecar["mounts"].extend([{"present": True}, "nonsense", {"root": "/x", "present": True, "free_bytes": "lots"}])
    mounts = sidecar_mounts(sidecar, thresholds=DEFAULT_THRESHOLDS)
    assert [m.root for m in mounts] == ["/", "/x"]
    assert mounts[1].level == "unknown"


def test_check_aggregates_hosts_worst_first() -> None:
    ok = _grade(_sidecar(_mount("/", free=400 * GiB)))
    stale = _grade(_sidecar(_mount("/", free=400 * GiB), age_hours=50.0))
    check = check_remote_capacity([ok, stale])
    assert check.name == "remote_capacity"
    assert check.level == "warn"
    assert check.details["hosts"]["mac-pro"]["level"] == "warn"

    failing = grade_remote_capacity("other", _sidecar(_mount("/", free=1 * GiB)), max_age_hours=36.0, now=NOW)
    assert check_remote_capacity([ok, failing]).level == "fail"
    assert check_remote_capacity([ok]).level == "ok"


def test_check_without_publishing_hosts_is_skipped() -> None:
    assert check_remote_capacity([]).level == "skipped"


def test_remote_capacity_is_an_opt_in_fail_on_token() -> None:
    assert "remote_capacity" in KNOWN_FAIL_ON_TOKENS
    assert "remote_capacity" not in DEFAULT_CHECK_FAIL_ON
