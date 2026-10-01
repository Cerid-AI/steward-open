# SPDX-License-Identifier: Apache-2.0

"""Estate-driven health: cloud File Provider roots, skipped FP sections, foreign_attach, estate_binding."""

from __future__ import annotations

import plistlib
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from steward.core.estate import CloudFP
from steward.core.fp_paths import claim_path_aliases, resolve_fp_paths
from steward.core.health.evaluate import check_estate_binding, check_foreign_attach
from steward.core.health.model import EstateSection
from steward.infra.db.admin import migrate
from steward.infra.dual_presence import default_mount_root, default_store_root
from steward.infra.estate import loader
from steward.infra.estate.active import cloud_fp_layout, cloud_fp_or_default
from steward.infra.fp_status import collect_fp_status
from steward.infra.health import attach
from steward.infra.health import collect as collect_mod
from steward.infra.health.attach import attached_images, foreign_attachments
from steward.infra.health.collect import collect_estate_health, estate_health_to_dict

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "estate" / "two-host.yml"
STORE = "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox"
IMAGE = "/Volumes/Level_3a/Studio-Photos.sparsebundle"


@pytest.fixture
def as_host(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    def _as(host: str) -> None:
        monkeypatch.setenv(loader.ESTATE_CONFIG_ENV, str(FIXTURE))
        monkeypatch.setenv("STEWARD_HOST", host)

    return _as


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "inventory.db"
    migrate(p)
    return p


def _health(db_path: Path, **kw: object) -> dict[str, object]:
    report = collect_estate_health(db_path=db_path, include_schedule=False, **kw)  # type: ignore[arg-type]
    return estate_health_to_dict(report)


def _checks(d: dict[str, object]) -> dict[str, str]:
    return {c["name"]: c["level"] for c in d["checks"]}  # type: ignore[attr-defined,index]


# ── cloud File Provider paths ─────────────────────────────────────────────────


def test_legacy_fp_roots_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    layout = cloud_fp_or_default()
    assert layout.store_root == STORE
    assert layout.mount_root == str(tmp_path / "Library" / "CloudStorage" / "Dropbox")
    assert layout.volume_root == "/Volumes/DropboxStorage"
    assert default_store_root() == Path(STORE)
    assert default_mount_root() == tmp_path / "Library" / "CloudStorage" / "Dropbox"
    report = collect_fp_status(home=tmp_path, probe_domain=False, probe_name_divergence=False)
    assert report.store_root == STORE
    assert report.mount_root == str(tmp_path / "Library" / "CloudStorage" / "Dropbox")


def test_mac_pro_fp_roots_come_from_the_estate(as_host: Callable[[str], None]) -> None:
    as_host("mac-pro")
    layout = cloud_fp_layout()
    assert layout is not None
    assert (layout.volume_id, layout.store_root) == ("macpro-dropbox", STORE)
    assert layout.mount_root == "/Users/operator/Library/CloudStorage/Dropbox"
    report = collect_fp_status(probe_domain=False, probe_name_divergence=False)
    assert report.mount_root == "/Users/operator/Library/CloudStorage/Dropbox"


def test_studio_has_no_cloud_fp(as_host: Callable[[str], None]) -> None:
    as_host("studio")
    assert cloud_fp_layout() is None


def test_fp_paths_follow_a_custom_layout() -> None:
    fp = CloudFP(
        provider="dropbox",
        store_root="/Volumes/Cloud/store",
        store_aliases=("/Volumes/Cloud/link",),
        mount_root="/Users/operator/CloudMount",
        cooling_off="t",
    )
    res = resolve_fp_paths("/Volumes/Cloud/link/a/b.txt", fp=fp)
    assert (res.unlink_path, res.store_path) == ("/Users/operator/CloudMount/a/b.txt", "/Volumes/Cloud/store/a/b.txt")
    assert claim_path_aliases("/Volumes/Cloud/store/a/b.txt", fp) == (
        "/Volumes/Cloud/store/a/b.txt",
        "/Users/operator/CloudMount/a/b.txt",
        "/Volumes/Cloud/link/a/b.txt",
    )
    # The legacy Dropbox store is not this layout's.
    assert resolve_fp_paths(f"{STORE}/a/b.txt", fp=fp).tier_hint is None


# ── health sections and checks ────────────────────────────────────────────────


def test_legacy_report_has_no_estate_checks(db_path: Path) -> None:
    d = _health(db_path, probes=False, include_fp=False)
    assert "estate" not in d
    assert not {"foreign_attach", "estate_binding"} & set(_checks(d))


def test_studio_skips_fp_sections(as_host: Callable[[str], None], db_path: Path) -> None:
    as_host("studio")
    d = _health(db_path, probes=False, include_fp=True, include_fp_domains=True)
    assert d["fp"]["level"] == "skipped"  # type: ignore[index]
    assert d["dual_presence"]["level"] == "skipped"  # type: ignore[index]
    assert d["fp_domains"] is None
    checks = _checks(d)
    assert checks["fp_not_ready"] == checks["dual_presence_poor"] == checks["fp_sync_stuck"] == "skipped"
    assert any("no cloud File Provider volume" in n for n in d["notes"])  # type: ignore[attr-defined]


def test_estate_binding_unpinned_is_ok_and_mismatch_fails(as_host: Callable[[str], None], db_path: Path) -> None:
    as_host("studio")
    d = _health(db_path, probes=False, include_fp=False)
    assert d["estate"]["binding"] == "unpinned"  # type: ignore[index]
    assert _checks(d)["estate_binding"] == "ok"
    assert _checks(d)["foreign_attach"] == "skipped"

    as_host("mac-pro")  # pins a machine_id this fresh DB does not carry
    d = _health(db_path, probes=False, include_fp=False)
    assert d["estate"]["binding"] == "mismatch"  # type: ignore[index]
    assert _checks(d)["estate_binding"] == "fail"


def test_unknown_host_fails_estate_binding() -> None:
    result = check_estate_binding(EstateSection(host_id=None, host_reason="hostname 'x' matches no host"))
    assert result.level == "fail"
    assert check_foreign_attach(EstateSection(host_id=None, host_reason="r")).level == "skipped"


# ── foreign_attach ────────────────────────────────────────────────────────────


def test_forbid_mount_present_fails(as_host: Callable[[str], None]) -> None:
    as_host("mac-pro")
    estate = loader.current()
    assert estate is not None
    found = foreign_attachments(estate, "mac-pro", images=[], live={"/Volumes/Level 2", "/Volumes/Studio Photos"})
    assert [(f.kind, f.volume_id, f.path) for f in found] == [
        ("forbid_mount", "studio-photos", "/Volumes/Studio Photos")
    ]
    section = EstateSection(host_id="mac-pro", host_reason="r", binding="bound", foreign=tuple(found))
    assert check_foreign_attach(section).level == "fail"


def test_exclusive_image_attached_on_non_owner_fails(as_host: Callable[[str], None]) -> None:
    as_host("mac-pro")
    estate = loader.current()
    assert estate is not None
    found = foreign_attachments(estate, "mac-pro", images=[IMAGE + "/"], live=set())
    assert [(f.kind, f.volume_id) for f in found] == [("exclusive_image", "studio-photos")]
    # The image's own host may attach it.
    assert foreign_attachments(estate, "studio", images=[IMAGE], live={"/Volumes/Studio Photos"}) == []
    section = EstateSection(host_id="studio", host_reason="r", binding="unpinned", foreign=())
    assert check_foreign_attach(section).level == "ok"


def test_collect_reports_foreign_attach_with_probes(
    as_host: Callable[[str], None], db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    as_host("mac-pro")
    monkeypatch.setattr(collect_mod, "probe_mounts", lambda **_kw: [])
    monkeypatch.setattr(collect_mod, "attached_images", lambda: [IMAGE])
    monkeypatch.setattr("steward.infra.estate.check.volumes_mounts", lambda: set())
    d = _health(db_path, probes=True, include_fp=False)
    assert _checks(d)["foreign_attach"] == "fail"
    assert d["estate"]["foreign"][0]["kind"] == "exclusive_image"  # type: ignore[index]


# ── hdiutil probe ─────────────────────────────────────────────────────────────


def _hdiutil_plist(*paths: str) -> bytes:
    images = [{"image-path": p, "system-entities": [{"mount-point": "/Volumes/X"}]} for p in paths]
    return plistlib.dumps({"framework": "1", "images": images})


def test_attached_images_is_a_noop_off_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attach.sys, "platform", "linux")
    assert attached_images() is None


def test_attached_images_parses_hdiutil_info(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=_hdiutil_plist(IMAGE), stderr=b"")

    monkeypatch.setattr(attach, "_hdiutil", lambda: "/usr/bin/hdiutil")
    monkeypatch.setattr(attach.subprocess, "run", fake_run)
    assert attached_images() == [IMAGE]
    assert calls == [["/usr/bin/hdiutil", "info", "-plist"]]


def test_attached_images_failure_is_unknown_not_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(attach, "_hdiutil", lambda: "/usr/bin/hdiutil")
    monkeypatch.setattr(attach.subprocess, "run", fake_run)
    assert attached_images() is None
