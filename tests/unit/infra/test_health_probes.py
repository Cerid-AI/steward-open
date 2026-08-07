# SPDX-License-Identifier: Apache-2.0

"""Unit tests for mount / tier live probes (ADR-0017)."""

from __future__ import annotations

from pathlib import Path

from steward.core.health.thresholds import HealthThresholds
from steward.infra.health.probes import (
    collect_mount_probes,
    discover_mount_roots,
    probe_mount,
    probe_one,
)


def test_probe_one_present_tmp(tmp_path: Path) -> None:
    root = tmp_path / "vol"
    root.mkdir()
    (root / "marker").write_text("x", encoding="utf-8")
    probe = probe_one(str(root), tier="L1")
    assert probe.present is True
    assert probe.tier == "L1"
    assert probe.root == str(root)
    assert probe.free_bytes is not None and probe.free_bytes >= 0
    assert probe.total_bytes is not None and probe.total_bytes > 0
    assert probe.sample_latency_ms is not None and probe.sample_latency_ms >= 0
    assert probe.error is None
    assert probe.level in ("ok", "warn")  # warn only if host truly low free


def test_probe_mount_missing_is_not_exception(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-volume"
    probe = probe_mount(missing, tier="L2", critical=False)
    assert probe.present is False
    assert probe.free_bytes is None
    assert probe.total_bytes is None
    assert probe.tier == "L2"
    assert probe.level == "warn"
    assert probe.sample_latency_ms is not None


def test_probe_critical_missing_is_fail(tmp_path: Path) -> None:
    missing = tmp_path / "dropbox-missing"
    probe = probe_one(str(missing), tier="DropboxStorage", critical=True)
    assert probe.present is False
    assert probe.level == "fail"


def test_probe_mount_infers_tier_from_path() -> None:
    probe = probe_mount("/Volumes/DropboxStorage/foo", tier=None)
    assert probe.tier == "DropboxStorage"
    # typically absent in Linux CI — still a probe result, not an exception
    assert probe.present is False


def test_collect_mount_probes_synthetic_only(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    probes = collect_mount_probes(
        roots=[(str(a), "boot", False), (str(b), "L1", False)],
        max_roots=8,
    )
    assert len(probes) == 2
    by_root = {p.root: p for p in probes}
    assert by_root[str(a)].present is True
    assert by_root[str(b)].present is False


def test_collect_mount_probes_respects_cap(tmp_path: Path) -> None:
    roots = [(str(tmp_path / f"r{i}"), "other-volume", False) for i in range(5)]
    for r, _, _ in roots:
        Path(r).mkdir()
    probes = collect_mount_probes(roots=roots, max_roots=2)
    assert len(probes) == 2


def test_discover_mount_roots_extra_only(tmp_path: Path) -> None:
    vol = tmp_path / "Level1"
    vol.mkdir()
    roots = discover_mount_roots(
        include_defaults=False,
        include_dropbox=False,
        include_home=False,
        extra_roots=[(str(vol), "L1", False)],
        home=tmp_path,
    )
    assert roots == [(str(vol), "L1", False)]


def test_low_free_threshold_forces_warn(tmp_path: Path) -> None:
    thr = HealthThresholds(
        free_bytes_min=10**18,
        free_ratio_min=0.99,
        sample_latency_warn_ms=1e12,
    )
    present = probe_one(str(tmp_path), tier="boot", thresholds=thr)
    assert present.present
    assert present.level == "warn"
