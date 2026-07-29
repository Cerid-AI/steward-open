# SPDX-License-Identifier: Apache-2.0

"""Tests for :mod:`steward.core.tiers`."""

from __future__ import annotations

import pytest

from steward.core.tiers import (
    CLOUD_FP_COOLING_OFF,
    CLOUD_FP_TIERS,
    LIVE_TIERS,
    NAS_READONLY_TIERS,
    TIER_PRIORITY,
    classify_tier,
)


@pytest.mark.parametrize(
    ("path", "expected_tier", "expected_volume"),
    [
        ("", "unknown", ""),
        ("/Users/sunrunner/x.txt", "boot", "boot-Users"),
        ("/private/etc/hosts", "boot", "boot-system"),
        ("/var/log/x.log", "boot", "boot-system"),
        ("/Volumes/Level 1/foo", "L1", "Level_1"),
        ("/Volumes/Level 1w/foo", "L1w", "Level_1w"),
        ("/Volumes/Level 2/foo", "L2", "Level_2"),
        ("/Volumes/Level_3a/foo", "L3a", "Level_3a"),
        ("/Volumes/NFS-Level3a/foo", "L3a", "Level_3a"),
        ("/Volumes/Backup/foo", "Backup", "Backup"),
        ("/Volumes/NFS-Backup/foo", "Backup", "Backup"),
        ("/Volumes/DropboxStorage/x", "DropboxStorage", "DropboxStorage"),
        (
            "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/a.bin",
            "DropboxStorage",
            "DropboxStorage",
        ),
        (
            "/Users/sunrunner/Library/CloudStorage/Dropbox/a.bin",
            "DropboxStorage",
            "Dropbox_CloudStorage",
        ),
        (
            "/Users/sunrunner/Library/CloudStorage/Dropbox-Personal/a.bin",
            "DropboxStorage",
            "Dropbox_CloudStorage",
        ),
        # Non-Dropbox under CloudStorage stays boot (under /Users).
        (
            "/Users/sunrunner/Library/CloudStorage/OneDrive/x",
            "boot",
            "boot-Users",
        ),
        ("/Volumes/BOOTCAMP/x", "BOOTCAMP", "BOOTCAMP"),
        ("/Volumes/SomeOtherDisk/x", "other-volume", "SomeOtherDisk"),
        ("/tmp/x", "unknown", ""),
    ],
)
def test_classify_tier(path: str, expected_tier: str, expected_volume: str) -> None:
    assert classify_tier(path) == (expected_tier, expected_volume)


def test_priority_ladder_is_monotone_for_live_tiers() -> None:
    # boot=0 < L1 < L1w < L2 < L3a < DropboxStorage < Backup
    ordered = ["boot", "L1", "L1w", "L2", "L3a", "DropboxStorage", "Backup"]
    priorities = [TIER_PRIORITY[t] for t in ordered]
    assert priorities == sorted(priorities)


def test_live_tiers_disjoint_from_nas_readonly() -> None:
    assert not (LIVE_TIERS & NAS_READONLY_TIERS)


def test_cloud_fp_tiers_are_live_subset() -> None:
    assert CLOUD_FP_TIERS <= LIVE_TIERS
    assert "DropboxStorage" in CLOUD_FP_TIERS
    assert CLOUD_FP_COOLING_OFF["DropboxStorage"].startswith("dropbox-cloud-trash")
