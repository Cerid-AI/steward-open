# SPDX-License-Identifier: Apache-2.0

"""Unit tests for :mod:`steward.infra.fp_status`."""
from __future__ import annotations

from pathlib import Path

from steward.infra.fp_status import collect_fp_status


def test_collect_fp_status_forked_tmp(tmp_path: Path) -> None:
    mount = tmp_path / "mount"
    store = tmp_path / "store"
    mount.mkdir()
    store.mkdir()
    (store / "logo.jpg").write_bytes(b"abc")
    (mount / "logo.jpg").write_bytes(b"abc")
    report = collect_fp_status(
        home=tmp_path,
        store_root=store,
        mount_root=mount,
        sample_rels=("logo.jpg", "missing"),
    )
    assert report.mount.exists
    assert report.store.exists
    # Same tmp volume → typically same st_dev; forked_devices may be False.
    assert report.sample_both == 1
    assert report.sample_neither == 1
    assert report.recommendations


def test_collect_fp_status_missing_roots(tmp_path: Path) -> None:
    report = collect_fp_status(
        home=tmp_path,
        store_root=tmp_path / "no-store",
        mount_root=tmp_path / "no-mount",
        sample_rels=(),
    )
    assert not report.mount.exists
    assert not report.store.exists
    # Both absent is not a device fork — just missing roots.
    assert report.forked_devices is False


def test_collect_fp_status_one_side_missing(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    report = collect_fp_status(
        home=tmp_path,
        store_root=store,
        mount_root=tmp_path / "no-mount",
        sample_rels=(),
    )
    assert report.store.exists
    assert not report.mount.exists
    assert report.forked_devices is True
