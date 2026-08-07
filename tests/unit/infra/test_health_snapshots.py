# SPDX-License-Identifier: Apache-2.0

"""Unit tests for health snapshot JSONL series (ADR-0017 §4)."""

from __future__ import annotations

import json
from pathlib import Path

from steward.core.health.model import (
    AdapterFreshness,
    EstateHealthReport,
    FPSection,
    HealthCheckResult,
    InventoryIntegrity,
    MountProbe,
    RootScanFreshness,
    StashHealth,
)
from steward.infra.health.collect import (
    estate_health_to_dict,
    estate_health_to_snapshot_dict,
)
from steward.infra.health.snapshots import (
    LATEST_FILENAME,
    SNAPSHOTS_FILENAME,
    health_dir,
    read_health_series,
    read_latest_pointer,
    write_health_snapshot,
)


def _minimal_report(
    *,
    generated_at: str = "2026-08-05T12:00:00+00:00",
    machine_id: str = "m-test",
    mounts: tuple[MountProbe, ...] = (),
) -> EstateHealthReport:
    return EstateHealthReport(
        generated_at=generated_at,
        machine_id=machine_id,
        overall="ok",
        inventory=InventoryIntegrity(
            permanodes=1,
            current_claims=2,
            scan_runs=1,
            audit_entries=10,
            machines=1,
            audit_ok=True,
            audit_skipped=False,
            counts_source="live",
        ),
        scan_freshness=(
            RootScanFreshness(
                root_path="/tmp/x",
                tier="boot",
                finished_at="2026-08-05T11:00:00+00:00",
                age_hours=1.0,
                scan_run_id=1,
                level="ok",
            ),
        ),
        stash=StashHealth(
            in_flight_entries=0,
            distinct_run_ids=0,
            oldest_ts_iso=None,
            newest_ts_iso=None,
            age_hours_oldest=None,
            cooling_off_days=7,
            grace_hours=24.0,
            overdue=None,
            source="skipped",
            level="skipped",
        ),
        adapters=AdapterFreshness(replicate=None, archive=None, level="unknown"),
        schedule=None,
        fp=FPSection(
            present=True,
            layout="external_drive_fp",
            cloud_retire_ready=True,
            problems=("noisy-problem",),
            warnings=("soft-warn",),
            notes=("bulky-note",),
            level="ok",
        ),
        attached_imports=(),
        mounts=mounts
        or (
            MountProbe(
                root="/Volumes/Level 1",
                tier="L1",
                present=True,
                free_bytes=1000,
                total_bytes=2000,
                sample_latency_ms=1.5,
                level="ok",
            ),
        ),
        rollups=None,
        checks=(
            HealthCheckResult(name="stale_scan", level="ok", message="fresh"),
        ),
        notes=("unit",),
        quick=True,
    )


def test_compact_snapshot_omits_bulky_fp_fields() -> None:
    report = _minimal_report()
    full = estate_health_to_dict(report)
    compact = estate_health_to_snapshot_dict(report, compact=True)
    assert "problems" in full["fp"]
    assert "notes" in full["fp"]
    assert compact["fp"]["cloud_retire_ready"] is True
    assert compact["fp"]["layout"] == "external_drive_fp"
    assert "problems" not in compact["fp"]
    assert "notes" not in compact["fp"]
    assert compact["inventory"]["current_claims"] == 2
    assert compact["mounts"][0]["free_bytes"] == 1000
    assert compact["checks"][0]["name"] == "stale_scan"
    assert "details" not in compact["checks"][0]


def test_write_and_read_series(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    r1 = _minimal_report(generated_at="2026-08-05T10:00:00+00:00")
    r2 = _minimal_report(generated_at="2026-08-05T11:00:00+00:00")
    path1 = write_health_snapshot(r1, data_dir=data_dir)
    path2 = write_health_snapshot(r2, data_dir=data_dir)
    assert path1 == path2
    assert path1.name == SNAPSHOTS_FILENAME
    assert (data_dir / "health" / LATEST_FILENAME).is_file()
    assert read_latest_pointer(data_dir=data_dir) == "2026-08-05T11:00:00+00:00"

    series = read_health_series(data_dir=data_dir, limit=10)
    assert len(series) == 2
    assert series[0]["generated_at"] == "2026-08-05T10:00:00+00:00"
    assert series[1]["generated_at"] == "2026-08-05T11:00:00+00:00"

    raw = path1.read_text(encoding="utf-8").strip().splitlines()
    assert len(raw) == 2
    for line in raw:
        obj = json.loads(line)
        assert "inventory" in obj
        assert "mounts" in obj


def test_read_series_limit(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    for i in range(5):
        write_health_snapshot(
            _minimal_report(generated_at=f"2026-08-05T1{i}:00:00+00:00"),
            data_dir=data_dir,
        )
    series = read_health_series(data_dir=data_dir, limit=2)
    assert len(series) == 2
    assert series[-1]["generated_at"] == "2026-08-05T14:00:00+00:00"


def test_prune_max_lines(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    for i in range(6):
        write_health_snapshot(
            _minimal_report(generated_at=f"2026-08-05T0{i}:00:00+00:00"),
            data_dir=data_dir,
            max_lines=3,
            max_age_days=90,
        )
    series = read_health_series(data_dir=data_dir, limit=100)
    assert len(series) == 3


def test_health_dir_layout(tmp_path: Path) -> None:
    assert health_dir(tmp_path) == tmp_path / "health"


def test_empty_series(tmp_path: Path) -> None:
    assert read_health_series(data_dir=tmp_path, limit=48) == []
    assert read_latest_pointer(data_dir=tmp_path) is None
