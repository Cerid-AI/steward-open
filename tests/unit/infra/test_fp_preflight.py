# SPDX-License-Identifier: Apache-2.0

"""Tests for apply-time FP health gate."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from steward.core.manifest_io import write_manifest
from steward.core.model.manifest import Manifest, ManifestHeader, ManifestRow
from steward.infra.fp_preflight import (
    fp_health_problems,
    fp_health_warnings,
    manifest_needs_fp_health,
)
from steward.infra.fp_status import (
    DomainProbe,
    FPHealthVerdict,
    FPStatusReport,
    PathProbe,
)


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


def _stub_report(**kwargs: object) -> FPStatusReport:
    base = dict(
        mount_root="/m",
        store_root="/s",
        mount=PathProbe("/m", True, True, 1),
        store=PathProbe("/s", True, True, 2),
        forked_devices=True,
        dual_samples=[],
        sample_both=2,
        sample_store_only=0,
        sample_mount_only=0,
        sample_neither=0,
        recommendations=[],
        notes=[],
        domain=None,
        dropbox_info_path="/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox",
        name_divergence=None,
        verdict=None,
    )
    base.update(kwargs)
    report = FPStatusReport(**base)  # type: ignore[arg-type]
    if report.verdict is None:
        from steward.infra.fp_status import evaluate_fp_health

        # rebuild with verdict
        v = evaluate_fp_health(report)
        report = FPStatusReport(
            mount_root=report.mount_root,
            store_root=report.store_root,
            mount=report.mount,
            store=report.store,
            forked_devices=report.forked_devices,
            dual_samples=report.dual_samples,
            sample_both=report.sample_both,
            sample_store_only=report.sample_store_only,
            sample_mount_only=report.sample_mount_only,
            sample_neither=report.sample_neither,
            recommendations=report.recommendations,
            notes=report.notes,
            domain=report.domain,
            dropbox_info_path=report.dropbox_info_path,
            name_divergence=report.name_divergence,
            verdict=v,
        )
    return report


def test_fp_health_external_drive_fork_not_problem(monkeypatch) -> None:
    report = _stub_report(
        domain=DomainProbe(
            provider_id="com.getdropbox.dropbox.fileprovider",
            domain_id="c1",
            connected=False,
            disconnected=True,
            disconnection_reason="This is an unlinked Dropbox",
            domain_path="FPFS_SHOULD_NOT_BE_USED",
            supports_syncing_trash=False,
        ),
        forked_devices=True,
        sample_both=3,
        sample_store_only=0,
    )
    monkeypatch.setattr("steward.infra.fp_preflight.collect_fp_status", lambda **_k: report)
    probs = fp_health_problems(prefer_mount_unlink=True)
    assert probs == []
    warns = fp_health_warnings(prefer_mount_unlink=True)
    assert any("residual" in w.lower() or "unlinked" in w.lower() for w in warns)
    assert any("different devices" in w.lower() or "external" in w.lower() for w in warns)


def test_fp_health_store_only_samples_problem(monkeypatch) -> None:
    report = _stub_report(
        sample_both=0,
        sample_store_only=3,
        forked_devices=True,
    )
    monkeypatch.setattr("steward.infra.fp_preflight.collect_fp_status", lambda **_k: report)
    probs = fp_health_problems(prefer_mount_unlink=True)
    assert any("store-only" in p.lower() for p in probs)


def test_fp_health_local_reclaim_needs_store(monkeypatch) -> None:
    report = FPStatusReport(
        mount_root="/m",
        store_root="/s",
        mount=PathProbe("/m", True, True, 1),
        store=PathProbe("/s", False, False, None),
        forked_devices=True,
        dual_samples=[],
        sample_both=0,
        sample_store_only=0,
        sample_mount_only=0,
        sample_neither=0,
        recommendations=[],
        notes=[],
        verdict=FPHealthVerdict(
            layout="mount_only",
            cloud_retire_ready=True,
            local_reclaim_ready=False,
            problems=(),
            warnings=(),
            notes=(),
        ),
    )
    monkeypatch.setattr("steward.infra.fp_preflight.collect_fp_status", lambda **_k: report)
    probs = fp_health_problems(prefer_mount_unlink=False)
    assert any("store" in p.lower() for p in probs)
