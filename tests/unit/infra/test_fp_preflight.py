# SPDX-License-Identifier: Apache-2.0

"""Tests for apply-time FP health gate."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from steward.core.manifest_io import write_manifest
from steward.core.model.manifest import Manifest, ManifestHeader, ManifestRow
from steward.infra.fp_preflight import (
    fp_health_problems,
    manifest_needs_fp_health,
)
from steward.infra.fp_status import FPStatusReport, PathProbe


def _manifest(tmp: Path, *, action: str, tier: str) -> Path:
    row = ManifestRow(
        action=action,  # type: ignore[arg-type]
        permanode_id="a" * 32,
        canonical_hash="b" * 64,
        size_bytes=1,
        source_path="/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/x",
        source_tier=tier,  # type: ignore[arg-type]
        destination_path=None,
        destination_tier="dropbox-cloud-trash-account-specific",
        rationale="test",
    )
    m = Manifest(
        header=ManifestHeader(
            produced_by_steward_version="test",
            produced_at=datetime.now(timezone.utc),
            policy_name="t",
            phase_name=None,
            manifest_run_id="r1",
        ),
        rows=(row,),
    )
    p = tmp / "m.tsv"
    write_manifest(p, m)
    return p


def test_manifest_needs_fp_for_retire_direct(tmp_path: Path) -> None:
    p = _manifest(tmp_path, action="retire_direct", tier="DropboxStorage")
    assert manifest_needs_fp_health(p) is True


def test_manifest_no_fp_for_l2_stash(tmp_path: Path) -> None:
    row = ManifestRow(
        action="stash",
        permanode_id="a" * 32,
        canonical_hash="b" * 64,
        size_bytes=1,
        source_path="/Volumes/Level 2/x",
        source_tier="L2",
        destination_path="/Volumes/Level 2/_s/x",
        destination_tier="L2",
        rationale="t",
    )
    m = Manifest(
        header=ManifestHeader(
            produced_by_steward_version="test",
            produced_at=datetime.now(timezone.utc),
            policy_name="t",
            phase_name=None,
            manifest_run_id="r2",
        ),
        rows=(row,),
    )
    p = tmp_path / "s.tsv"
    write_manifest(p, m)
    assert manifest_needs_fp_health(p) is False


def test_fp_health_problems_with_forked_stub(monkeypatch) -> None:
    def fake_status(**_kw: object) -> FPStatusReport:
        return FPStatusReport(
            mount_root="/m",
            store_root="/s",
            mount=PathProbe("/m", True, True, 1),
            store=PathProbe("/s", True, True, 2),
            forked_devices=True,
            dual_samples=[],
            sample_both=0,
            sample_store_only=3,
            sample_mount_only=0,
            sample_neither=0,
            recommendations=[],
            notes=[],
        )

    monkeypatch.setattr(
        "steward.infra.fp_preflight.collect_fp_status", fake_status
    )
    probs = fp_health_problems(prefer_mount_unlink=True)
    assert any("fork" in p.lower() or "different devices" in p for p in probs)
