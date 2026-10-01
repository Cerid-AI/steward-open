# SPDX-License-Identifier: Apache-2.0

"""Live mounts vs the estate (``check_mounts``), with the mount table simulated."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from steward.core.estate import Estate
from steward.infra.estate import check as check_mod
from steward.infra.estate import loader
from steward.infra.estate.check import Finding, check_mounts, volumes_mounts

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "estate" / "two-host.yml"

MAC_PRO_ALL = {
    "/Volumes/Level 1",
    "/Volumes/Level 1w",
    "/Volumes/Level 2",
    "/Volumes/DropboxStorage",
    "/Volumes/BOOTCAMP",
    "/Volumes/Level_3a",
    "/Volumes/Backup",
}
STUDIO_ALL = {"/Volumes/Work", "/Volumes/Cache", "/Volumes/Studio Photos", "/Volumes/Level_3a", "/Volumes/Backup"}


@pytest.fixture
def estate() -> Estate:
    return loader.load_estate(FIXTURE)


def _kinds(findings: list[Finding]) -> set[tuple[str, str, str | None]]:
    return {(f.level, f.kind, f.path) for f in findings}


def test_everything_declared_and_mounted_is_clean(estate: Estate) -> None:
    assert check_mounts(estate, "mac-pro", live=MAC_PRO_ALL | {"/Volumes/operator"}) == []
    assert check_mounts(estate, "studio", live=STUDIO_ALL) == []


def test_missing_critical_is_a_problem_and_normal_a_warning(estate: Estate) -> None:
    live = MAC_PRO_ALL - {"/Volumes/Level 2", "/Volumes/BOOTCAMP"}
    assert _kinds(check_mounts(estate, "mac-pro", live=live)) == {
        ("problem", "missing_mount", "/Volumes/Level 2"),
        ("warning", "missing_mount", "/Volumes/BOOTCAMP"),
    }


def test_forbidden_mount_present_is_a_problem(estate: Estate) -> None:
    findings = check_mounts(estate, "mac-pro", live=MAC_PRO_ALL | {"/Volumes/Studio Photos"})
    assert _kinds(findings) == {("problem", "forbidden_mount", "/Volumes/Studio Photos")}
    assert findings[0].volume_id == "studio-photos"


def test_undeclared_mount_is_a_warning(estate: Estate) -> None:
    findings = check_mounts(estate, "mac-pro", live=MAC_PRO_ALL | {"/Volumes/USB Stick"})
    assert _kinds(findings) == {("warning", "unexpected_mount", "/Volumes/USB Stick")}


def test_another_hosts_volume_is_unexpected_here(estate: Estate) -> None:
    findings = check_mounts(estate, "mac-pro", live=MAC_PRO_ALL | {"/Volumes/Work"})
    assert _kinds(findings) == {("warning", "unexpected_mount", "/Volumes/Work")}


def test_ignored_mount_may_be_absent(estate: Estate) -> None:
    assert check_mounts(estate, "mac-pro", live=MAC_PRO_ALL) == []


def test_aliases_and_probe_skip_are_not_unexpected(estate: Estate) -> None:
    live = MAC_PRO_ALL | {"/Volumes/NFS-Level3a", "/Volumes/Level 00"}
    assert check_mounts(estate, "mac-pro", live=live) == []


def test_non_volumes_mount_paths_only_need_to_exist(estate: Estate, monkeypatch: pytest.MonkeyPatch) -> None:
    vols = dict(estate.volumes)
    work = vols["studio-work"]
    vols["studio-work"] = work.model_copy(
        update={"mounts": {"studio": work.mounts["studio"].model_copy(update={"path": "/srv/work"})}}
    )
    custom = estate.model_copy(update={"volumes": vols})
    live = STUDIO_ALL - {"/Volumes/Work"}
    monkeypatch.setattr(check_mod.os.path, "exists", lambda p: os.fspath(p) == "/srv/work")
    assert check_mounts(custom, "studio", live=live) == []
    monkeypatch.setattr(check_mod.os.path, "exists", lambda p: False)
    assert _kinds(check_mounts(custom, "studio", live=live)) == {("problem", "missing_mount", "/srv/work")}


def test_volumes_mounts_lists_only_mountpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("A", "B", "plain-dir"):
        (tmp_path / name).mkdir()
    mounted = {str(tmp_path / "A"), str(tmp_path / "B")}
    monkeypatch.setattr(check_mod.os.path, "ismount", lambda p: os.fspath(p) in mounted)
    assert volumes_mounts(tmp_path) == mounted
    assert volumes_mounts(tmp_path / "absent") == set()
