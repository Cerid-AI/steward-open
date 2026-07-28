# SPDX-License-Identifier: Apache-2.0

"""Unit tests for :mod:`steward.core.fp_paths` (ADR-0015)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from steward.core.fp_paths import (
    claim_path_aliases,
    dropbox_mount_path,
    dropbox_relative,
    dropbox_store_path,
    is_dropbox_path,
    is_icloud_mount_path,
    resolve_fp_paths,
)


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def test_dropbox_relative_from_store() -> None:
    p = "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/AI/x.bin"
    assert dropbox_relative(p) == "AI/x.bin"


def test_dropbox_relative_from_symlink_store() -> None:
    p = "/Volumes/DropboxStorage/Dropbox/AI/x.bin"
    assert dropbox_relative(p) == "AI/x.bin"


def test_dropbox_relative_from_mount(fake_home: Path) -> None:
    p = str(fake_home / "Library/CloudStorage/Dropbox/AI/x.bin")
    assert dropbox_relative(p) == "AI/x.bin"


def test_resolve_prefers_mount_unlink(fake_home: Path) -> None:
    """Verify and unlink are the same mount path (never store≠mount)."""
    claim = "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/AI/x.bin"
    r = resolve_fp_paths(claim, prefer_mount_unlink=True)
    assert r.used_mount_for_unlink is True
    mount = str(fake_home / "Library/CloudStorage/Dropbox/AI/x.bin")
    assert r.unlink_path == mount
    assert r.verify_path == mount  # logic law: same path
    assert r.verify_path == r.unlink_path
    assert r.tier_hint == "DropboxStorage"
    assert r.store_path == claim
    assert r.mount_path == r.unlink_path


def test_resolve_allow_store_path_unlink(fake_home: Path) -> None:
    claim = "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/AI/x.bin"
    r = resolve_fp_paths(claim, prefer_mount_unlink=False)
    assert r.used_mount_for_unlink is False
    assert r.unlink_path == claim
    assert r.verify_path == claim


def test_non_fp_passthrough() -> None:
    claim = "/Volumes/Level 2/docs/a.txt"
    r = resolve_fp_paths(claim)
    assert r.unlink_path == claim
    assert r.verify_path == claim
    assert r.tier_hint is None
    assert r.used_mount_for_unlink is False


def test_is_dropbox_and_icloud(fake_home: Path) -> None:
    assert is_dropbox_path("/Volumes/DropboxStorage/Dropbox/x")
    assert not is_dropbox_path("/Volumes/Backup/x")
    icloud = str(fake_home / "Library/Mobile Documents/com~apple~CloudDocs/a")
    assert is_icloud_mount_path(icloud)
    assert not is_icloud_mount_path("/tmp/a")


def test_claim_path_aliases_include_store_and_mount(fake_home: Path) -> None:
    claim = "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/AI/x.bin"
    aliases = claim_path_aliases(claim)
    assert claim in aliases
    assert dropbox_store_path("AI/x.bin") in aliases
    assert dropbox_mount_path("AI/x.bin") in aliases
    assert any(a.startswith("/Volumes/DropboxStorage/Dropbox/") for a in aliases)


def test_roundtrip_store_mount(fake_home: Path) -> None:
    rel = "Justin Work/report.pdf"
    store = dropbox_store_path(rel)
    mount = dropbox_mount_path(rel)
    assert dropbox_relative(store) == rel
    assert dropbox_relative(mount) == rel
    assert os.environ["HOME"] == str(fake_home)
