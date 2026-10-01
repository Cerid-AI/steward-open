# SPDX-License-Identifier: Apache-2.0

"""Health probe roots from the estate: legacy parity, and the two hosts of the shared fixture."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from steward.core.health.evaluate import check_mount_missing
from steward.core.health.model import MountProbe
from steward.infra.estate import loader
from steward.infra.health import probes as probes_mod
from steward.infra.health.probes import UNMANAGED_TIER, collect_mount_probes, discover_mount_roots

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "estate" / "two-host.yml"
HOME = Path("/Users/operator")
STORE = "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox"
CLOUD_MOUNT = "/Users/operator/Library/CloudStorage/Dropbox"

STUDIO_LIVE = {"/Volumes/Work", "/Volumes/Cache", "/Volumes/Studio Photos", "/Volumes/Level_3a", "/Volumes/Backup"}
MAC_PRO_LIVE = {
    "/Volumes/Level 1",
    "/Volumes/Level 1w",
    "/Volumes/Level 2",
    "/Volumes/DropboxStorage",
    "/Volumes/BOOTCAMP",
    "/Volumes/Level_3a",
    "/Volumes/Backup",
    "/Volumes/operator",
}


@pytest.fixture
def as_host(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    def _as(host: str) -> None:
        monkeypatch.setenv(loader.ESTATE_CONFIG_ENV, str(FIXTURE))
        monkeypatch.setenv("STEWARD_HOST", host)

    return _as


def _fake_probe(present: set[str]) -> Callable[..., MountProbe]:
    def probe_one(root: str, *, tier: str | None = None, critical: bool = False, thresholds: object = None) -> MountProbe:
        here = root in present
        return MountProbe(root=root, tier=tier, present=here, level="ok" if here else "fail" if critical else "warn")

    return probe_one


# ── legacy (no estate file) ───────────────────────────────────────────────────


def test_legacy_default_roots_unchanged(tmp_path: Path) -> None:
    roots = discover_mount_roots(home=tmp_path, include_dropbox=False)
    assert roots[:8] == [
        (str(tmp_path), "boot", False),
        ("/Volumes/Level 1", "L1", False),
        ("/Volumes/Level 1w", "L1w", False),
        ("/Volumes/Level 2", "L2", False),
        ("/Volumes/Level_3a", "L3a", False),
        ("/Volumes/Backup", "Backup", False),
        ("/Volumes/DropboxStorage", "DropboxStorage", True),
        (STORE, "DropboxStorage", True),
    ]


def test_legacy_dropbox_roots_unchanged(tmp_path: Path) -> None:
    roots = discover_mount_roots(home=tmp_path, include_defaults=False, include_home=False)
    assert roots == [
        (STORE, "DropboxStorage", True),
        (str(tmp_path / "Library" / "CloudStorage" / "Dropbox"), "DropboxStorage", True),
    ]


def test_legacy_criticality_is_still_dropbox_only(tmp_path: Path) -> None:
    probes = collect_mount_probes(
        roots=[(str(tmp_path / "gone-dropbox"), "DropboxStorage"), (str(tmp_path / "gone-l1"), "L1")],
        max_roots=4,
    )
    assert [(p.tier, p.level) for p in probes] == [("DropboxStorage", "fail"), ("L1", "warn")]


# ── estate ────────────────────────────────────────────────────────────────────


def test_studio_probes_its_mounts_and_no_dropbox(as_host: Callable[[str], None]) -> None:
    as_host("studio")
    roots = discover_mount_roots(home=HOME, live=STUDIO_LIVE)
    assert roots == [
        (str(HOME), "boot", True),
        ("/Volumes/Work", "Work", True),
        ("/Volumes/Cache", "Cache", False),
        ("/Volumes/Studio Photos", "StudioPhotos", True),
        ("/Volumes/Level_3a", "L3a", False),
        ("/Volumes/Backup", "Backup", False),
    ]


def test_studio_mount_missing_passes(as_host: Callable[[str], None], monkeypatch: pytest.MonkeyPatch) -> None:
    present = STUDIO_LIVE | {str(HOME)}
    monkeypatch.setattr(probes_mod, "volumes_mounts", lambda: STUDIO_LIVE)
    monkeypatch.setattr(probes_mod, "probe_one", _fake_probe(present))

    # Without an estate the same machine fails mount_missing on the Dropbox roots it has never had.
    legacy = collect_mount_probes(home=HOME)
    assert check_mount_missing(legacy).level == "fail"

    as_host("studio")
    probes = collect_mount_probes(home=HOME)
    assert not any(p.tier == "DropboxStorage" for p in probes)
    assert check_mount_missing(probes).level == "ok"


def test_mac_pro_keeps_the_current_probe_set(as_host: Callable[[str], None]) -> None:
    as_host("mac-pro")
    roots = discover_mount_roots(home=HOME, live=MAC_PRO_LIVE | {"/Volumes/Level 00"})
    legacy = (
        [str(HOME)]
        + [r for r, _, _ in probes_mod._DEFAULT_TIER_ROOTS]
        + [str(HOME / "Library" / "CloudStorage" / "Dropbox")]
    )
    assert {r for r, _, _ in roots} == set(legacy) | {"/Volumes/BOOTCAMP"}
    by_root = {r: (t, c) for r, t, c in roots}
    assert by_root["/Volumes/DropboxStorage"] == ("DropboxStorage", True)
    assert by_root[STORE] == ("DropboxStorage", True)
    assert by_root[CLOUD_MOUNT] == ("DropboxStorage", True)
    assert by_root["/Volumes/Level 2"] == ("L2", True)
    assert by_root["/Volumes/Level 1"] == ("L1", False)
    # ignore (the other host's home share), forbid and probe_skip are not probed.
    assert not {"/Volumes/operator", "/Volumes/Studio Photos", "/Volumes/Level 00"} & set(by_root)


def test_undeclared_volume_is_unmanaged_and_ungraded(as_host: Callable[[str], None], tmp_path: Path) -> None:
    as_host("studio")
    stick = tmp_path / "USB Stick"
    stick.mkdir()
    roots = discover_mount_roots(home=HOME, live=STUDIO_LIVE | {str(stick)})
    assert roots[-1] == (str(stick), UNMANAGED_TIER, False)

    probe = collect_mount_probes(roots=[roots[-1]])[0]
    assert probe.present is True
    assert probe.level == "skipped"
    assert probe.message.startswith("unmanaged")


def test_tier_inferred_from_the_estate(as_host: Callable[[str], None]) -> None:
    as_host("studio")
    assert probes_mod.probe_one("/Volumes/Work/nope-not-here").tier == "Work"
