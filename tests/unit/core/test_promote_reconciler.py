# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the PromotionPolicy reconciler."""

from __future__ import annotations

import pytest

from steward.core.policy.reconciler import ClaimSnapshot, reconcile_promote
from steward.core.policy.schema import (
    PathTranslation,
    PromotionDefaults,
    PromotionPhase,
    PromotionPolicy,
)


@pytest.fixture
def policy() -> PromotionPolicy:
    return PromotionPolicy(
        version=1,
        kind="PromotionPolicy",
        defaults=PromotionDefaults(
            path_translations=[
                PathTranslation.model_validate({"from": "/Volumes/NFS-Backup/", "to": "/Volumes/Backup/"}),
            ],
            source_tier="Backup",
        ),
        phases=[
            PromotionPhase(
                name="photos",
                match={"domain": "photos"},
                destination_root="/Volumes/Level 2/Photos",
            ),
            PromotionPhase(
                name="music",
                match={"domain": "music"},
                destination_root="/Volumes/Level_3a/Music",
                max_files=2,
            ),
        ],
    )


def _snap(claim_id: int, pid: str, tier: str, path: str, *, domain: str | None = None) -> ClaimSnapshot:
    return ClaimSnapshot(
        claim_id=claim_id,
        permanode_id=pid,
        canonical_hash="0" * 64,
        machine_id="m1",
        file_path=path,
        tier=tier,
        size_bytes=1000,
        domain=domain,
    )


def test_only_backup_only_permanodes_are_promoted(policy: PromotionPolicy) -> None:
    """A permanode that already has a live-tier copy is NOT promoted."""
    pid_only = "a" * 32
    pid_also_on_l2 = "b" * 32
    claims = [
        _snap(1, pid_only, "Backup", "/Volumes/Backup/photos/img.jpg", domain="photos"),
        _snap(2, pid_also_on_l2, "Backup", "/Volumes/Backup/photos/already.jpg", domain="photos"),
        _snap(3, pid_also_on_l2, "L2", "/Volumes/Level 2/photos/already.jpg", domain="photos"),
    ]
    m = reconcile_promote(claims=claims, policy=policy, steward_version="t")
    assert len(m.rows) == 1
    assert m.rows[0].permanode_id == pid_only


def test_domain_match(policy: PromotionPolicy) -> None:
    pid = "a" * 32
    claims = [_snap(1, pid, "Backup", "/Volumes/Backup/photos/img.jpg", domain="photos")]
    m = reconcile_promote(claims=claims, policy=policy, steward_version="t")
    assert m.rows[0].destination_path == "/Volumes/Level 2/Photos/img.jpg"


def test_path_translation_applied(policy: PromotionPolicy) -> None:
    """The NFS-Backup → Backup translation should rewrite the source path."""
    pid = "a" * 32
    claims = [_snap(1, pid, "Backup", "/Volumes/NFS-Backup/photos/x.jpg", domain="photos")]
    m = reconcile_promote(claims=claims, policy=policy, steward_version="t")
    assert m.rows[0].source_path == "/Volumes/Backup/photos/x.jpg"


def test_no_matching_phase_skips_permanode(policy: PromotionPolicy) -> None:
    pid = "a" * 32
    claims = [_snap(1, pid, "Backup", "/Volumes/Backup/x.txt", domain="documents")]
    m = reconcile_promote(claims=claims, policy=policy, steward_version="t")
    assert m.rows == ()


def test_phase_name_filter(policy: PromotionPolicy) -> None:
    """``phase_name='music'`` excludes photos rows even when they'd otherwise match."""
    photos = "a" * 32
    music = "b" * 32
    claims = [
        _snap(1, photos, "Backup", "/Volumes/Backup/p.jpg", domain="photos"),
        _snap(2, music, "Backup", "/Volumes/Backup/song.mp3", domain="music"),
    ]
    m = reconcile_promote(claims=claims, policy=policy, steward_version="t", phase_name="music")
    assert len(m.rows) == 1
    assert m.rows[0].permanode_id == music


def test_max_files_caps_output(policy: PromotionPolicy) -> None:
    pids = [chr(ord("a") + i) * 32 for i in range(5)]
    claims = [_snap(i, pids[i], "Backup", f"/Volumes/Backup/song{i}.mp3", domain="music") for i in range(5)]
    m = reconcile_promote(claims=claims, policy=policy, steward_version="t", max_files=2)
    assert len(m.rows) == 2


# ───────────────────────── mirror-path resolver ─────────────────────────────


def _mirror_policy(
    *,
    mirror_from: str | None = None,
    mirror_strip_prefix: str | None = None,
) -> PromotionPolicy:
    """Build a one-phase policy for testing path resolution."""
    return PromotionPolicy(
        version=1,
        kind="PromotionPolicy",
        defaults=PromotionDefaults(source_tier="Backup", mirror_strip_prefix=mirror_strip_prefix),
        phases=[
            PromotionPhase(
                name="photos",
                match={"domain": "photos"},
                destination_root="/Volumes/Level 2/Photos-Heritage",
                mirror_from=mirror_from,
            ),
        ],
    )


def test_mirror_from_sentinel_preserves_subdirs() -> None:
    """A `mirror_from: "Photos/"` sentinel anchors the mirror — everything
    after the LAST occurrence is preserved under destination_root."""
    pid = "a" * 32
    src = "/Volumes/Backup/Clones/SomeMac/Photos/2024/IMG_001.jpg"
    claims = [_snap(1, pid, "Backup", src, domain="photos")]
    policy = _mirror_policy(mirror_from="Photos/")
    m = reconcile_promote(claims=claims, policy=policy, steward_version="t")
    assert m.rows[0].destination_path == "/Volumes/Level 2/Photos-Heritage/2024/IMG_001.jpg"


def test_mirror_from_sentinel_uses_last_occurrence() -> None:
    """When the sentinel appears multiple times in the path, the LAST one wins."""
    pid = "a" * 32
    # Both `/Photos/` and `/Photos/` show up — the suffix is anchored at the last.
    src = "/Volumes/Backup/Photos/Old/Photos/2020/x.jpg"
    claims = [_snap(1, pid, "Backup", src, domain="photos")]
    policy = _mirror_policy(mirror_from="Photos/")
    m = reconcile_promote(claims=claims, policy=policy, steward_version="t")
    assert m.rows[0].destination_path == "/Volumes/Level 2/Photos-Heritage/2020/x.jpg"


def test_mirror_from_sentinel_miss_falls_back_to_basename() -> None:
    """When the sentinel isn't in the path AND no strip-prefix is set,
    fall back to basename (back-compat behavior)."""
    pid = "a" * 32
    src = "/Volumes/Backup/randompath/IMG_002.jpg"  # no "Photos/" in path
    claims = [_snap(1, pid, "Backup", src, domain="photos")]
    policy = _mirror_policy(mirror_from="Photos/")
    m = reconcile_promote(claims=claims, policy=policy, steward_version="t")
    assert m.rows[0].destination_path == "/Volumes/Level 2/Photos-Heritage/IMG_002.jpg"


def test_mirror_strip_prefix_preserves_subdirs() -> None:
    """`defaults.mirror_strip_prefix` strips the tier-mount prefix and
    mirrors everything below under destination_root."""
    pid = "a" * 32
    src = "/Volumes/Backup/Clones/Mac/Photos/2024/IMG_001.jpg"
    claims = [_snap(1, pid, "Backup", src, domain="photos")]
    policy = _mirror_policy(mirror_strip_prefix="/Volumes/Backup/")
    m = reconcile_promote(claims=claims, policy=policy, steward_version="t")
    assert m.rows[0].destination_path == ("/Volumes/Level 2/Photos-Heritage/Clones/Mac/Photos/2024/IMG_001.jpg")


def test_mirror_from_overrides_strip_prefix() -> None:
    """When both are set, the phase-local sentinel takes precedence over
    the policy-wide strip-prefix."""
    pid = "a" * 32
    src = "/Volumes/Backup/Clones/Mac/Photos/2024/IMG_001.jpg"
    claims = [_snap(1, pid, "Backup", src, domain="photos")]
    policy = _mirror_policy(mirror_from="Photos/", mirror_strip_prefix="/Volumes/Backup/")
    m = reconcile_promote(claims=claims, policy=policy, steward_version="t")
    # Sentinel wins → suffix from after `Photos/`, not from after `/Volumes/Backup/`.
    assert m.rows[0].destination_path == "/Volumes/Level 2/Photos-Heritage/2024/IMG_001.jpg"


def test_no_mirror_config_back_compat_basename() -> None:
    """With neither mirror_from nor mirror_strip_prefix set, behavior is
    unchanged from v0.1.0 (basename flat copy)."""
    pid = "a" * 32
    src = "/Volumes/Backup/Clones/Mac/Photos/2024/IMG_001.jpg"
    claims = [_snap(1, pid, "Backup", src, domain="photos")]
    policy = _mirror_policy()
    m = reconcile_promote(claims=claims, policy=policy, steward_version="t")
    assert m.rows[0].destination_path == "/Volumes/Level 2/Photos-Heritage/IMG_001.jpg"


def test_mirror_path_avoids_collisions() -> None:
    """Two source paths with the same basename under different subdirs
    must produce different destinations when mirror_from is set."""
    pid_a = "a" * 32
    pid_b = "b" * 32
    claims = [
        _snap(1, pid_a, "Backup", "/Volumes/Backup/Mac1/Photos/2024/IMG_001.jpg", domain="photos"),
        _snap(2, pid_b, "Backup", "/Volumes/Backup/Mac2/Photos/2024/IMG_001.jpg", domain="photos"),
    ]
    policy = _mirror_policy(mirror_strip_prefix="/Volumes/Backup/")
    m = reconcile_promote(claims=claims, policy=policy, steward_version="t")
    destinations = {r.destination_path for r in m.rows}
    assert len(destinations) == 2, "mirror-path resolver must not collide"


def test_mirror_path_with_path_translation() -> None:
    """Path translations apply BEFORE mirror resolution — verify both compose."""
    pid = "a" * 32
    # Source on disk: /Volumes/NFS-Backup/Mac/Photos/2024/x.jpg
    # After translation: /Volumes/Backup/Mac/Photos/2024/x.jpg
    # After mirror_from "Photos/": Photos suffix = "2024/x.jpg"
    src = "/Volumes/NFS-Backup/Mac/Photos/2024/x.jpg"
    claims = [_snap(1, pid, "Backup", src, domain="photos")]
    policy = PromotionPolicy(
        version=1,
        kind="PromotionPolicy",
        defaults=PromotionDefaults(
            source_tier="Backup",
            path_translations=[
                PathTranslation.model_validate({"from": "/Volumes/NFS-Backup/", "to": "/Volumes/Backup/"}),
            ],
        ),
        phases=[
            PromotionPhase(
                name="photos",
                match={"domain": "photos"},
                destination_root="/Volumes/Level 2/Photos-Heritage",
                mirror_from="Photos/",
            ),
        ],
    )
    m = reconcile_promote(claims=claims, policy=policy, steward_version="t")
    assert m.rows[0].source_path == "/Volumes/Backup/Mac/Photos/2024/x.jpg"
    assert m.rows[0].destination_path == "/Volumes/Level 2/Photos-Heritage/2024/x.jpg"
