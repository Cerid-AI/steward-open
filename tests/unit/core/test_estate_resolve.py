# SPDX-License-Identifier: Apache-2.0

"""Tests for :mod:`steward.core.estate.resolve`, including legacy parity with ``classify_tier``."""

from __future__ import annotations

import re
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from steward.core.errors import EstateError
from steward.core.estate import Estate, HostResolver, VolumeMatch, classify, default_estate
from steward.core.estate.legacy import CLOUD_FP_COOLING_OFF, LEGACY_HOST

from .test_tiers import CLASSIFY_TIER_CASES

# ── legacy parity ─────────────────────────────────────────────────────────────

_OTHER_VOLUME_RE = re.compile(r"^/Volumes/([^/]+)/")
_CLOUDSTORAGE_DROPBOX_RE = re.compile(r"/Library/CloudStorage/Dropbox(?:-[^/]+)?(?:/|$)")


def _reference_classify_tier(path: str) -> tuple[str, str]:
    """``core/tiers.py::classify_tier`` as of 0.3.27, verbatim — the parity oracle."""
    if not path:
        return ("unknown", "")
    if _CLOUDSTORAGE_DROPBOX_RE.search(path):
        return ("DropboxStorage", "Dropbox_CloudStorage")
    if path.startswith("/Users/"):
        return ("boot", "boot-Users")
    if path.startswith("/private/") or path.startswith("/var/"):
        return ("boot", "boot-system")
    if path.startswith("/Volumes/Level 1w"):
        return ("L1w", "Level_1w")
    if path.startswith("/Volumes/Level 1/"):
        return ("L1", "Level_1")
    if path.startswith("/Volumes/Level 2/"):
        return ("L2", "Level_2")
    if path.startswith("/Volumes/Level_3a/"):
        return ("L3a", "Level_3a")
    if path.startswith("/Volumes/NFS-Level3a/"):
        return ("L3a", "Level_3a")
    if path.startswith("/Volumes/Backup/"):
        return ("Backup", "Backup")
    if path.startswith("/Volumes/NFS-Backup/"):
        return ("Backup", "Backup")
    if path.startswith("/Volumes/DropboxStorage"):
        return ("DropboxStorage", "DropboxStorage")
    if path.startswith("/Volumes/BOOTCAMP/"):
        return ("BOOTCAMP", "BOOTCAMP")
    if path.startswith("/Volumes/"):
        m = _OTHER_VOLUME_RE.match(path)
        return ("other-volume", m.group(1) if m else "")
    return ("unknown", "")


def _legacy(path: str) -> tuple[str, str]:
    m = classify(path, LEGACY_HOST, default_estate())
    return (m.tier, m.label)


@pytest.mark.parametrize(("path", "expected_tier", "expected_volume"), CLASSIFY_TIER_CASES)
def test_legacy_estate_matches_classify_tier_table(path: str, expected_tier: str, expected_volume: str) -> None:
    assert _legacy(path) == (expected_tier, expected_volume)


# Boundary paths where a prefix-order or trailing-slash slip would show.
_EDGE_PATHS = [
    "/",
    "/Users",
    "/Users/",
    "/private",
    "/var",
    "/Volumes",
    "/Volumes/",
    "/Volumes/Level 1",
    "/Volumes/Level 1w",
    "/Volumes/Level 1wide/x",
    "/Volumes/Level 1/",
    "/Volumes/Level 2",
    "/Volumes/Level 20/x",
    "/Volumes/Level_3a",
    "/Volumes/Level_3ab/x",
    "/Volumes/Backup",
    "/Volumes/Backups/x",
    "/Volumes/DropboxStorage",
    "/Volumes/DropboxStorage2/x",
    "/Volumes/BOOTCAMP",
    "/Volumes/Studio Photos/x",
    "/Volumes/Level 2/Library/CloudStorage/Dropbox/x",
    "/Volumes/Level 2/Library/CloudStorage/Dropbox",
    "/Volumes/Level 2/Library/CloudStorage/DropboxX/x",
    "/Users/a/Library/CloudStorage/Dropbox-Team Name/x",
    "/Users/a/Library/CloudStorage/Dropbox-/x",
    "/opt/homebrew/x",
    "relative/path",
    "Volumes/Level 1/x",
]


@pytest.mark.parametrize("path", _EDGE_PATHS)
def test_legacy_estate_matches_reference_on_edges(path: str) -> None:
    assert _legacy(path) == _reference_classify_tier(path)


_SEGMENTS = st.sampled_from(
    [
        "Users",
        "private",
        "var",
        "Volumes",
        "Level 1",
        "Level 1w",
        "Level 2",
        "Level_3a",
        "NFS-Level3a",
        "Backup",
        "NFS-Backup",
        "DropboxStorage",
        "BOOTCAMP",
        "Library",
        "CloudStorage",
        "Dropbox",
        "Dropbox-Personal",
        "OneDrive",
        "Other Disk",
        "x",
        "",
    ]
)


@given(st.lists(_SEGMENTS, max_size=7), st.booleans(), st.booleans())
def test_legacy_estate_matches_reference_property(segments: list[str], absolute: bool, trailing: bool) -> None:
    path = ("/" if absolute else "") + "/".join(segments) + ("/" if trailing and segments else "")
    assert _legacy(path) == _reference_classify_tier(path)


@given(st.text(max_size=60))
def test_legacy_estate_matches_reference_on_arbitrary_text(path: str) -> None:
    assert _legacy(path) == _reference_classify_tier(path)


def test_legacy_cloud_fp_matches_fp_paths() -> None:
    cfp = default_estate().volumes["dropbox"].cloud_fp
    assert cfp is not None
    # The store prefixes core/fp_paths.py hardcoded before it read them from the layout.
    assert (cfp.store_root + "/", *(a + "/" for a in cfp.store_aliases)) == (
        "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/",
        "/Volumes/DropboxStorage/Dropbox/",
    )
    assert cfp.mount_root == "~/Library/CloudStorage/Dropbox"
    assert cfp.cooling_off == CLOUD_FP_COOLING_OFF["DropboxStorage"]


def test_legacy_estate_grants_local_rw_everywhere() -> None:
    resolver = HostResolver(default_estate(), LEGACY_HOST)
    for path, tier, _ in CLASSIFY_TIER_CASES:
        m = resolver.classify(path)
        assert m.access == ("ignore" if tier == "unknown" else "rw"), path


# ── precedence ────────────────────────────────────────────────────────────────


def _estate(volumes: dict[str, Any]) -> Estate:
    return Estate.model_validate({"version": 1, "hosts": {"h": {"role": "primary"}}, "volumes": volumes})


def _vol(tier: str, path: str, **mount: Any) -> dict[str, Any]:
    return {"tier": tier, "owner": "h", "mounts": {"h": {"path": path, "access": "rw", **mount}}}


def test_longest_prefix_wins_regardless_of_declaration_order() -> None:
    estate = _estate({"outer": _vol("Outer", "/Volumes/Work"), "inner": _vol("Inner", "/Volumes/Work/scratch")})
    assert classify("/Volumes/Work/scratch/a", "h", estate).volume_id == "inner"
    assert classify("/Volumes/Work/scratch", "h", estate).volume_id == "inner"
    assert classify("/Volumes/Work/scratchpad/a", "h", estate).volume_id == "outer"
    assert classify("/Volumes/Work/a", "h", estate).volume_id == "outer"


def test_mount_path_matches_itself_and_below_only() -> None:
    estate = _estate({"work": _vol("Work", "/Volumes/Work")})
    assert classify("/Volumes/Work", "h", estate).volume_id == "work"
    assert classify("/Volumes/Work/a", "h", estate).volume_id == "work"
    assert classify("/Volumes/Workshop/a", "h", estate) == VolumeMatch(None, "other-volume", "Workshop", "ignore")


def test_regex_beats_longer_prefix() -> None:
    estate = _estate(
        {
            "boot": _vol("boot", "/", prefixes=["/Users/"]),
            "fp": {
                **_vol("Cloud", "/Volumes/FP", match_regex=r"/Library/CloudStorage/FP(?:/|$)", regex_label="FP_mount"),
                "kind": "cloud-fp",
                "cloud_fp": {"provider": "fp", "store_root": "/Volumes/FP", "mount_root": "~/x", "cooling_off": "t"},
            },
        }
    )
    m = classify("/Users/a/Library/CloudStorage/FP/doc", "h", estate)
    assert (m.volume_id, m.tier, m.label) == ("fp", "Cloud", "FP_mount")
    assert classify("/Volumes/FP/doc", "h", estate).label == "FP"
    assert classify("/Users/a/doc", "h", estate).volume_id == "boot"


def test_alias_counts_as_prefix_and_anchors_root() -> None:
    estate = _estate({"nas": {**_vol("L3a", "/Volumes/Level_3a"), "aliases": ["/Volumes/NFS-Level3a/"]}})
    m = classify("/Volumes/NFS-Level3a/a", "h", estate)
    assert (m.volume_id, m.root) == ("nas", "/Volumes/NFS-Level3a")
    assert classify("/Volumes/Level_3a/a", "h", estate).root == "/Volumes/Level_3a"


def test_prefixes_replace_mount_path() -> None:
    estate = _estate({"boot": _vol("boot", "/", prefixes=["/Users/", {"prefix": "/var/", "label": "sys"}])})
    assert classify("/Users/a", "h", estate).label == "boot"
    assert classify("/var/x", "h", estate).label == "sys"
    assert classify("/opt/x", "h", estate) == VolumeMatch(None, "unknown", "", "ignore")


def test_boot_without_prefixes_is_a_catch_all() -> None:
    estate = _estate({"boot": _vol("boot", "/"), "work": _vol("Work", "/Volumes/Work")})
    assert classify("/opt/x", "h", estate).volume_id == "boot"
    assert classify("/Volumes/Work/x", "h", estate).volume_id == "work"


def test_default_label_is_mount_basename() -> None:
    estate = _estate({"photos": _vol("StudioPhotos", "/Volumes/Studio Photos")})
    assert classify("/Volumes/Studio Photos/a", "h", estate).label == "Studio Photos"


def test_unknown_host_raises(two_host_estate: Estate) -> None:
    with pytest.raises(EstateError, match="laptop"):
        classify("/Volumes/Work/x", "laptop", two_host_estate)


# ── same path, different host ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "host", "volume_id", "tier", "access"),
    [
        ("/Volumes/Level_3a/x", "studio", "nas-l3a", "L3a", "rw"),
        ("/Volumes/Level_3a/x", "mac-pro", "nas-l3a", "L3a", "ro"),
        ("/Volumes/NFS-Level3a/x", "mac-pro", "nas-l3a", "L3a", "ro"),
        ("/Volumes/Backup/_steward-mirror/mac-pro/db", "studio", "nas-backup", "Backup", "ro"),
        ("/Volumes/Backup/_steward-mirror/mac-pro/db", "mac-pro", "nas-backup", "Backup", "ro"),
        ("/Volumes/Studio Photos/Library/x", "studio", "studio-photos", "StudioPhotos", "rw"),
        ("/Volumes/Studio Photos/Library/x", "mac-pro", "studio-photos", "StudioPhotos", "forbid"),
        ("/Users/operator/x", "studio", "studio-boot", "boot", "rw"),
        ("/Users/operator/x", "mac-pro", "macpro-boot", "boot", "rw"),
        ("/Volumes/operator/Develop/x", "mac-pro", "studio-boot", "boot", "ignore"),
        ("/Users/operator/Library/CloudStorage/Dropbox/a", "mac-pro", "macpro-dropbox", "DropboxStorage", "rw"),
        ("/Users/operator/Library/CloudStorage/Dropbox/a", "studio", "studio-boot", "boot", "rw"),
        ("/Volumes/Level 2/steward-data/inventory.db", "mac-pro", "macpro-l2", "L2", "rw"),
    ],
)
def test_two_host_classification(
    two_host_estate: Estate, path: str, host: str, volume_id: str, tier: str, access: str
) -> None:
    m = classify(path, host, two_host_estate)
    assert (m.volume_id, m.tier, m.access) == (volume_id, tier, access)


@pytest.mark.parametrize(
    ("path", "host", "tier", "label"),
    [
        ("/Volumes/Work/x", "mac-pro", "other-volume", "Work"),
        ("/Volumes/Level 2/x", "studio", "other-volume", "Level 2"),
        ("/opt/homebrew/bin/x", "studio", "unknown", ""),
    ],
)
def test_two_host_unmanaged_paths(two_host_estate: Estate, path: str, host: str, tier: str, label: str) -> None:
    assert classify(path, host, two_host_estate) == VolumeMatch(None, tier, label, "ignore")
