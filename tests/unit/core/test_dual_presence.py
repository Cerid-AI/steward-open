# SPDX-License-Identifier: Apache-2.0

"""Unit tests for pure dual-presence classification (ADR-0020)."""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.core.dual_presence import (
    CLOUD_SAFE_KINDS,
    LOCAL_RECLAIM_KINDS,
    classify_presence_kind,
    cloud_safe_ratio,
    is_conflict_relative,
    kinds_for_intent,
    map_claim_to_pair,
)


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def test_conflict_relative_detection() -> None:
    assert is_conflict_relative("AI/foo (Selective Sync Conflict)/x.bin")
    assert is_conflict_relative("AI/foo (Selective Sync Conflict)")
    assert not is_conflict_relative("AI/normal/x.bin")
    assert not is_conflict_relative(None)
    assert not is_conflict_relative("")


@pytest.mark.parametrize(
    ("se", "me", "rel", "store_err", "mount_err", "outside", "expected"),
    [
        (True, True, "a/b", False, False, False, "dual"),
        (True, False, "a/b", False, False, False, "store_only"),
        (False, True, "a/b", False, False, False, "mount_only"),
        (False, False, "a/b", False, False, False, "missing_store"),
        (True, True, "x (Selective Sync Conflict)/y", False, False, False, "conflict_name_path"),
        (True, True, "a/b", False, True, False, "mount_error"),
        (None, None, None, False, False, True, "outside_store_root"),
        (None, None, "a", False, False, False, "unknown"),
        (False, None, "a", True, False, False, "missing_store"),
    ],
)
def test_classify_matrix(
    se: bool | None,
    me: bool | None,
    rel: str | None,
    store_err: bool,
    mount_err: bool,
    outside: bool,
    expected: str,
) -> None:
    kind = classify_presence_kind(
        store_exists=se,
        mount_exists=me,
        relative=rel,
        store_error=store_err,
        mount_error=mount_err,
        outside_store_root=outside,
    )
    assert kind == expected


def test_map_claim_store_and_mount(fake_home: Path) -> None:
    claim = "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/AI/x.bin"
    mapped = map_claim_to_pair(claim)
    assert mapped.relative == "AI/x.bin"
    assert mapped.store_path == claim
    assert mapped.mount_path == str(fake_home / "Library/CloudStorage/Dropbox/AI/x.bin")
    assert mapped.kind == "unknown"  # no existence probe in core


def test_map_claim_conflict() -> None:
    claim = (
        "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/"
        "AI/foo (Selective Sync Conflict)/x.bin"
    )
    mapped = map_claim_to_pair(claim)
    assert mapped.kind == "conflict_name_path"
    assert mapped.relative is not None
    assert "Selective Sync Conflict" in mapped.relative


def test_map_outside() -> None:
    mapped = map_claim_to_pair("/Volumes/Level 2/docs/a.txt")
    assert mapped.kind == "outside_store_root"


def test_map_explicit_roots() -> None:
    store = "/tmp/store-root"
    mount = "/tmp/mount-root"
    mapped = map_claim_to_pair(
        "/tmp/store-root/a/b.txt",
        store_root=store,
        mount_root=mount,
    )
    assert mapped.relative == "a/b.txt"
    assert mapped.store_path == "/tmp/store-root/a/b.txt"
    assert mapped.mount_path == "/tmp/mount-root/a/b.txt"


def test_cloud_and_local_safe_sets() -> None:
    assert CLOUD_SAFE_KINDS == frozenset({"dual"})
    assert LOCAL_RECLAIM_KINDS == frozenset({"dual", "store_only"})
    assert kinds_for_intent("cloud_retire") == CLOUD_SAFE_KINDS
    assert kinds_for_intent("local_reclaim") == LOCAL_RECLAIM_KINDS
    assert kinds_for_intent("observe") is None


def test_cloud_safe_ratio() -> None:
    assert cloud_safe_ratio(dual=7, store_only=3) == pytest.approx(0.7)
    assert cloud_safe_ratio(dual=0, store_only=0) is None
    assert cloud_safe_ratio(dual=0, store_only=5) == 0.0
