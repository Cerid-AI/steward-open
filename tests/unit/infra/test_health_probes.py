# SPDX-License-Identifier: Apache-2.0

"""Unit tests for mount / tier live probes (ADR-0017)."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from steward.core.health.thresholds import HealthThresholds
from steward.infra.health import probes as probes_mod
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
    # Thresholds are pinned wide open rather than left at defaults. This
    # asserted `level in ("ok", "warn")` against whatever free space the
    # developer's disk happened to have, and started failing the moment this
    # machine crossed into the new `fail` band — the test was grading the host,
    # not the probe.
    generous = HealthThresholds(
        free_bytes_min=0,
        free_ratio_min=0.0,
        free_bytes_fail=0,
        free_ratio_fail=0.0,
        sample_latency_warn_ms=1e12,
    )
    assert probe_one(str(root), tier="L1", thresholds=generous).level == "ok"


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
        # The fail floors must be pinned too, or this test's outcome depends
        # on the host's actual free space: on a machine sitting under the
        # default 3% ratio floor the probe grades "fail" and the assertion
        # below flips. Left at defaults it passed or failed by disk.
        free_bytes_fail=0,
        free_ratio_fail=0.0,
        sample_latency_warn_ms=1e12,
    )
    present = probe_one(str(tmp_path), tier="boot", thresholds=thr)
    assert present.present
    assert present.level == "warn"


def test_low_free_threshold_forces_fail(tmp_path: Path) -> None:
    """Free space must be able to reach `fail`, not cap at `warn`."""
    thr = HealthThresholds(
        free_bytes_fail=10**18,
        free_ratio_fail=0.99,
        free_bytes_min=10**18,
        free_ratio_min=0.99,
        sample_latency_warn_ms=1e12,
    )
    probe = probe_one(str(tmp_path), tier="boot", thresholds=thr)
    assert probe.present
    assert probe.level == "fail"


def test_symlinked_root_is_skipped_not_scored_twice(tmp_path: Path) -> None:
    """/Volumes/Level 00 is a symlink to / — one disk, two findings."""
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(target)
    probe = probe_one(str(link), tier="boot")
    assert probe.level == "skipped"
    assert "symlink" in probe.message


def test_read_only_mount_is_skipped(tmp_path: Path, monkeypatch) -> None:
    """A read-only volume's free space is not actionable from this host.

    The mounted release DMG sits permanently at 0 bytes free; grading it
    warned forever, which is how a report teaches people to ignore it.
    """
    real = os.statvfs(tmp_path)
    # Copy the real result and only flip the read-only bit: shutil.disk_usage
    # calls os.statvfs too, so a stub carrying just f_flag breaks it.
    read_only = SimpleNamespace(
        **{name: getattr(real, name) for name in dir(real) if name.startswith("f_")},
    )
    read_only.f_flag = real.f_flag | os.ST_RDONLY
    monkeypatch.setattr(os, "statvfs", lambda _p: read_only)
    probe = probe_one(str(tmp_path), tier="boot")
    assert probe.level == "skipped"
    assert "read-only" in probe.message


def test_impossible_disk_usage_is_not_ok(tmp_path: Path, monkeypatch) -> None:
    """The NFS tiers report more free than total; that must not read healthy."""
    monkeypatch.setattr(probes_mod.shutil, "disk_usage", lambda _p: _Usage())
    probe = probe_one(str(tmp_path), tier="L3a")
    assert probe.level == "unknown"


class _Usage:
    """Mirrors the real /Volumes/Backup reading: free far exceeds total."""

    total = 32 * 1024**3
    used = 0
    free = 815 * 1024**3
