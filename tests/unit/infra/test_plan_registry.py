# SPDX-License-Identifier: Apache-2.0

"""Unit tests for data-dir plan backlog registry (ADR-0019)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from steward.core.manifest_io import write_manifest
from steward.core.model.manifest import Manifest, ManifestHeader, ManifestRow
from steward.infra.plans.registry import (
    list_plans,
    prune_plans,
    register_plan_from_manifest,
    show_plan,
)


def _write_mini_manifest(path: Path, *, plan_id: str = "a" * 32, rows: int = 2) -> Path:
    # permanode_id must be 32 chars
    mrows = []
    for i in range(rows):
        pid = f"{i:032x}"
        mrows.append(
            ManifestRow(
                action="stash",
                permanode_id=pid,
                canonical_hash="b" * 64,
                size_bytes=100 + i,
                source_path=f"/tmp/file{i}.txt",
                source_tier="L2",
                destination_path=f"/tmp/stash/file{i}.txt",
                destination_tier="L2",
                rationale="test",
            )
        )
    manifest = Manifest(
        header=ManifestHeader(
            produced_by_steward_version="0.0.0-test",
            produced_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            policy_name="retention.yml",
            phase_name="dedup",
            manifest_run_id=plan_id,
        ),
        rows=tuple(mrows),
    )
    write_manifest(path, manifest)
    return path


def test_register_list_show_roundtrip(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    man = _write_mini_manifest(tmp_path / "plan.tsv", plan_id="c" * 32, rows=3)
    rec = register_plan_from_manifest(
        man,
        data_dir=data,
        policy_name="retention.yml",
        policy_kind="RetentionPolicy",
        machine_id="m-test",
    )
    assert rec.plan_id == "c" * 32
    assert rec.rows_total == 3
    assert rec.estimated_bytes == 100 + 101 + 102
    assert rec.action_counts.get("stash") == 3
    assert rec.status in ("registered", "blocked")

    shown = show_plan(rec.plan_id, data_dir=data)
    assert shown is not None
    assert shown.rows_total == 3
    assert shown.manifest_path.endswith("plan.tsv")

    listed = list_plans(data_dir=data, limit=10)
    assert len(listed) == 1
    assert listed[0].plan_id == rec.plan_id

    # last-writer-wins: re-register updates index
    rec2 = register_plan_from_manifest(
        man,
        data_dir=data,
        policy_name="retention.yml",
        policy_kind="RetentionPolicy",
        machine_id="m-test",
    )
    listed2 = list_plans(data_dir=data)
    assert len(listed2) == 1
    assert listed2[0].plan_id == rec2.plan_id


def test_empty_plan_blocked(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    man = _write_mini_manifest(tmp_path / "empty.tsv", plan_id="d" * 32, rows=0)
    # write_manifest with 0 rows still needs header — Manifest allows empty rows
    rec = register_plan_from_manifest(man, data_dir=data, machine_id="m")
    assert "empty_plan" in rec.blocked_reasons
    assert rec.status == "blocked"


def test_prune_requires_flag(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="dry_run|execute"):
        prune_plans(data_dir=tmp_path, older_than_days=1)


def test_prune_dry_run(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    man = _write_mini_manifest(tmp_path / "p.tsv", plan_id="e" * 32)
    register_plan_from_manifest(man, data_dir=data, machine_id="m")
    result = prune_plans(data_dir=data, older_than_days=0, dry_run=True)
    assert result["ok"] is True
    assert result["count"] >= 1
    # still present
    assert show_plan("e" * 32, data_dir=data) is not None
