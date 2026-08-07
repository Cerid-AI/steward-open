# SPDX-License-Identifier: Apache-2.0

"""Unit tests for infra dual-presence probe + plan filter (ADR-0020)."""

from __future__ import annotations

import json
from pathlib import Path

from steward.infra.dual_presence import (
    collect_dual_presence_stats,
    dual_presence_stats_to_dict,
    filter_plan_rows,
    probe_pair,
    write_filtered_plans,
)


def _touch(path: Path, data: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_probe_pair_buckets(tmp_path: Path) -> None:
    store = tmp_path / "store"
    mount = tmp_path / "mount"
    _touch(store / "both.txt", b"aa")
    _touch(mount / "both.txt", b"aa")
    _touch(store / "only_store.txt", b"bb")
    _touch(mount / "only_mount.txt", b"cc")

    dual = probe_pair(store / "both.txt", mount / "both.txt", relative="both.txt")
    assert dual.kind == "dual"
    assert dual.store_exists is True
    assert dual.mount_exists is True

    so = probe_pair(store / "only_store.txt", mount / "only_store.txt", relative="only_store.txt")
    assert so.kind == "store_only"

    mo = probe_pair(store / "only_mount.txt", mount / "only_mount.txt", relative="only_mount.txt")
    assert mo.kind == "mount_only"

    missing = probe_pair(store / "gone.txt", mount / "gone.txt", relative="gone.txt")
    assert missing.kind == "missing_store"

    conflict = probe_pair(
        store / "x (Selective Sync Conflict)" / "f.txt",
        mount / "x (Selective Sync Conflict)" / "f.txt",
        relative="x (Selective Sync Conflict)/f.txt",
    )
    assert conflict.kind == "conflict_name_path"


def test_filter_plan_rows_and_write(tmp_path: Path) -> None:
    store = tmp_path / "store"
    mount = tmp_path / "mount"
    _touch(store / "a.txt", b"1")
    _touch(mount / "a.txt", b"1")
    _touch(store / "b.txt", b"2")
    # conflict-named under store only
    conflict_rel = "c (Selective Sync Conflict)/z.txt"
    _touch(store / "c (Selective Sync Conflict)" / "z.txt", b"3")

    rows = [
        {"source_path": str(store / "a.txt"), "action": "retire_direct"},
        {"source_path": str(store / "b.txt"), "action": "retire_direct"},
        {"source_path": str(store / conflict_rel), "action": "retire_direct"},
        {"source_path": str(tmp_path / "outside.txt"), "action": "retire_direct"},
    ]
    result = filter_plan_rows(
        rows,
        store_root=store,
        mount_root=mount,
        intent="cloud_retire",
        comments=["# test plan"],
        input_plan="mem",
    )
    assert result.stats.dual == 1
    assert result.stats.store_only == 1
    assert result.stats.conflict_name_path == 1
    assert result.stats.outside_store_root == 1
    assert len(result.buckets["dual"]) == 1
    assert result.buckets["dual"][0]["source_path"].endswith("a.txt")

    out = tmp_path / "out"
    artifacts = write_filtered_plans(result, out_dir=out)
    assert Path(artifacts.stats_path).is_file()
    stats = json.loads(Path(artifacts.stats_path).read_text(encoding="utf-8"))
    assert stats["dual"] == 1
    assert stats["store_only"] == 1
    dual_tsv = out / "plan-dual.tsv"
    assert dual_tsv.is_file()
    body = dual_tsv.read_text(encoding="utf-8")
    assert "a.txt" in body
    assert "b.txt" not in body.split("source_path")[-1] or body.count("b.txt") == 0
    # dual bucket file should only contain dual row
    data_lines = [ln for ln in body.splitlines() if ln and not ln.startswith("#")]
    assert any("a.txt" in ln for ln in data_lines)
    assert not any("b.txt" in ln for ln in data_lines if not ln.startswith("source_path"))


def test_collect_stats_counts(tmp_path: Path) -> None:
    store = tmp_path / "store"
    mount = tmp_path / "mount"
    _touch(store / "x.txt")
    _touch(mount / "x.txt")
    _touch(store / "y.txt")
    stats = collect_dual_presence_stats(
        [str(store / "x.txt"), str(store / "y.txt"), str(store / "missing.txt")],
        store_root=store,
        mount_root=mount,
    )
    assert stats.counted == 3
    assert stats.dual == 1
    assert stats.store_only == 1
    assert stats.missing_store == 1
    d = dual_presence_stats_to_dict(stats)
    assert d["cloud_safe_sample_ratio"] == 0.5
    assert "cloud_safe_kinds" in d
