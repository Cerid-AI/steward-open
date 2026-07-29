# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the policy reconciler (dedup-retire)."""

from __future__ import annotations

from steward.core.policy.reconciler import ClaimSnapshot, reconcile_dedup_retire
from steward.core.policy.schema import (
    DedupRetire,
    RetentionExclusions,
    RetentionPolicy,
)

_POLICY = RetentionPolicy(
    version=1,
    kind="RetentionPolicy",
    exclusions=RetentionExclusions(
        always_skip_substrings=["/@eaDir/", "/.fseventsd/"],
        basename_prefixes=["._"],
        basename_exact=[".DS_Store"],
    ),
    dedup_retire=DedupRetire(
        tier_priority={"boot": 0, "L1": 1, "L2": 3, "L3a": 4, "Backup": 6},
        live_tiers=["boot", "L1", "L2", "L3a"],
        cooling_off_days=7,
        stash_roots={"L2": "/Volumes/Level 2/_cooling-off-stash"},
        nas_manifest_tiers=["Backup"],
    ),
)


def _snap(claim_id: int, pid: str, tier: str, path: str, size: int = 1000) -> ClaimSnapshot:
    return ClaimSnapshot(
        claim_id=claim_id,
        permanode_id=pid,
        canonical_hash="0" * 64,
        machine_id="m1",
        file_path=path,
        tier=tier,
        size_bytes=size,
    )


def test_dropbox_tier_emits_retire_direct_not_stash() -> None:
    """Cloud-FP tiers must never plan same-FS stash (ADR-0014)."""
    policy = RetentionPolicy(
        version=1,
        kind="RetentionPolicy",
        exclusions=RetentionExclusions(),
        dedup_retire=DedupRetire(
            tier_priority={
                "boot": 0,
                "L1": 1,
                "L2": 3,
                "DropboxStorage": 5,
                "Backup": 6,
            },
            live_tiers=["boot", "L1", "L2", "DropboxStorage"],
            cooling_off_days=7,
            stash_roots={"L2": "/Volumes/Level 2/_cooling-off-stash"},
            nas_manifest_tiers=["Backup"],
        ),
    )
    claims = [
        _snap(1, "a" * 32, "L1", "/Volumes/Level 1/canonical.bin"),
        _snap(
            2,
            "a" * 32,
            "DropboxStorage",
            "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/dup.bin",
        ),
    ]
    m = reconcile_dedup_retire(claims=claims, policy=policy, steward_version="test")
    assert len(m.rows) == 1
    row = m.rows[0]
    assert row.action == "retire_direct"
    assert row.destination_path is None
    assert row.destination_tier == "dropbox-cloud-trash-account-specific"
    assert "Dropbox" in row.source_path


def test_keeper_is_highest_priority_tier() -> None:
    # Same permanode at L1, L2, Backup → keep L1, stash L2, NAS-manifest Backup.
    claims = [
        _snap(1, "a" * 32, "L2", "/Volumes/Level 2/x"),
        _snap(2, "a" * 32, "L1", "/Volumes/Level 1/x"),
        _snap(3, "a" * 32, "Backup", "/Volumes/Backup/x"),
    ]
    m = reconcile_dedup_retire(
        claims=claims,
        policy=_POLICY,
        steward_version="test",
    )
    assert len(m.rows) == 2
    actions = sorted(r.action for r in m.rows)
    assert actions == ["nas_manifest", "stash"]
    stash_row = next(r for r in m.rows if r.action == "stash")
    assert stash_row.source_path == "/Volumes/Level 2/x"
    assert stash_row.destination_path is not None
    assert "_cooling-off-stash" in stash_row.destination_path


def test_singleton_is_not_planned() -> None:
    """A permanode with only one current claim should never appear in a plan."""
    claims = [_snap(1, "lonely", "L2", "/Volumes/Level 2/only.txt")]
    m = reconcile_dedup_retire(
        claims=claims,
        policy=_POLICY,
        steward_version="test",
    )
    assert m.rows == ()


def test_noise_paths_filtered() -> None:
    """Noise paths must not appear in the plan, even when they would otherwise dedup."""
    claims = [
        _snap(1, "a" * 32, "L1", "/Volumes/Level 1/.fseventsd/x"),
        _snap(2, "a" * 32, "L2", "/Volumes/Level 2/.fseventsd/x"),
    ]
    m = reconcile_dedup_retire(
        claims=claims,
        policy=_POLICY,
        steward_version="test",
    )
    assert m.rows == ()


def test_root_prefix_filter() -> None:
    """Claims outside ``root_prefix`` must not appear in the plan."""
    claims = [
        _snap(1, "a" * 32, "L1", "/Volumes/Level 1/x"),
        _snap(2, "a" * 32, "L2", "/Volumes/Level 2/x"),
    ]
    m = reconcile_dedup_retire(
        claims=claims,
        policy=_POLICY,
        steward_version="test",
        root_prefix="/Volumes/Level 2/",
    )
    # L1 claim is filtered out → only one claim remains in the group → no plan.
    assert m.rows == ()


def test_manifest_header_carries_provenance() -> None:
    claims = [
        _snap(1, "a" * 32, "L1", "/Volumes/Level 1/x"),
        _snap(2, "a" * 32, "L2", "/Volumes/Level 2/x"),
    ]
    m = reconcile_dedup_retire(
        claims=claims,
        policy=_POLICY,
        steward_version="0.42.0",
        manifest_run_id="fixed-id",
    )
    assert m.header.produced_by_steward_version == "0.42.0"
    assert m.header.manifest_run_id == "fixed-id"
    assert m.header.policy_name == "retention.yml"
    assert m.header.phase_name == "dedup-retire"


def test_stash_destination_uses_policy_stash_root_when_set() -> None:
    """When the policy supplies a stash_root for the tier, use it."""
    claims = [
        _snap(1, "a" * 32, "L1", "/Volumes/Level 1/x"),
        _snap(2, "a" * 32, "L2", "/Volumes/Level 2/x"),
    ]
    m = reconcile_dedup_retire(
        claims=claims,
        policy=_POLICY,
        steward_version="test",
        manifest_run_id="run-9",
    )
    stash_row = next(r for r in m.rows if r.action == "stash")
    assert stash_row.destination_path is not None
    # Policy supplied /Volumes/Level 2/_cooling-off-stash for L2.
    assert stash_row.destination_path.startswith("/Volumes/Level 2/_cooling-off-stash")
    assert "run-9" in stash_row.destination_path


def test_stash_destination_falls_back_to_same_dir_without_policy_root() -> None:
    """Tiers without a configured stash_root get a same-dir fallback."""
    policy = RetentionPolicy(
        version=1,
        kind="RetentionPolicy",
        exclusions=RetentionExclusions(),
        dedup_retire=DedupRetire(
            tier_priority={"L1": 1, "L2": 3},
            live_tiers=["L1", "L2"],
            stash_roots={},  # no policy stash root
            nas_manifest_tiers=[],
        ),
    )
    claims = [
        _snap(1, "b" * 32, "L1", "/Volumes/Level 1/foo.txt"),
        _snap(2, "b" * 32, "L2", "/Volumes/Level 2/foo.txt"),
    ]
    m = reconcile_dedup_retire(claims=claims, policy=policy, steward_version="t")
    stash_row = next(r for r in m.rows if r.action == "stash")
    assert stash_row.destination_path is not None
    assert "/Volumes/Level 2/_cooling-off-stash" in stash_row.destination_path


# ─────────────────────── recovered-substrings bias ──────────────────────────


def _policy_with_recovered(substrings: list[str]) -> RetentionPolicy:
    return RetentionPolicy(
        version=1,
        kind="RetentionPolicy",
        exclusions=RetentionExclusions(),
        dedup_retire=DedupRetire(
            tier_priority={"L1": 1, "L2": 3, "Backup": 6},
            live_tiers=["L1", "L2"],
            stash_roots={},
            nas_manifest_tiers=["Backup"],
            recovered_substrings=substrings,
        ),
    )


def test_recovered_bias_forces_non_recovered_keeper_over_tier_priority() -> None:
    """A mixed group (some claims recovered, some not) must always pick a
    non-recovered keeper — even when the recovered claim is on a better tier."""
    policy = _policy_with_recovered(["/RECOVERED-"])
    pid = "a" * 32
    claims = [
        # Recovered claim on L1 (would normally be keeper by tier priority).
        _snap(1, pid, "L1", "/Volumes/Level 1/RECOVERED-20240101/foo.txt"),
        # Non-recovered claim on L2 (worse tier) — bias makes it the keeper.
        _snap(2, pid, "L2", "/Volumes/Level 2/foo.txt"),
    ]
    m = reconcile_dedup_retire(claims=claims, policy=policy, steward_version="t")
    assert len(m.rows) == 1
    stash_row = m.rows[0]
    assert stash_row.action == "stash"
    assert "/RECOVERED-" in stash_row.source_path
    assert stash_row.source_tier == "L1"  # Recovered L1 claim got stashed.


def test_recovered_bias_inactive_when_all_claims_are_recovered() -> None:
    """When EVERY claim in a group is recovered, the bias has no effect —
    tier priority decides as usual."""
    policy = _policy_with_recovered(["/RECOVERED-"])
    pid = "a" * 32
    claims = [
        _snap(1, pid, "L1", "/Volumes/Level 1/RECOVERED-A/foo.txt"),
        _snap(2, pid, "L2", "/Volumes/Level 2/RECOVERED-B/foo.txt"),
    ]
    m = reconcile_dedup_retire(claims=claims, policy=policy, steward_version="t")
    assert len(m.rows) == 1
    # L1 still wins by priority; L2 stashed.
    assert m.rows[0].source_tier == "L2"


def test_recovered_substrings_empty_preserves_v0_1_0_behavior() -> None:
    """With no recovered_substrings configured, the reconciler behaves
    identically to v0.1.0 (tier priority is the sole discriminator)."""
    policy = _policy_with_recovered([])  # empty list
    pid = "a" * 32
    claims = [
        _snap(1, pid, "L1", "/Volumes/Level 1/RECOVERED-A/foo.txt"),
        _snap(2, pid, "L2", "/Volumes/Level 2/foo.txt"),
    ]
    m = reconcile_dedup_retire(claims=claims, policy=policy, steward_version="t")
    # L1 wins by tier priority (no recovered bias to override it).
    assert m.rows[0].source_tier == "L2"


def test_recovered_bias_against_nas_manifest_tier() -> None:
    """Recovered claim on the NAS-readonly tier (Backup) → nas_manifest row,
    NOT a stash row. The bias decides keeper-vs-stashed; the action is
    still chosen by tier classification."""
    policy = _policy_with_recovered(["/RECOVERED-"])
    pid = "a" * 32
    claims = [
        # Recovered claim on Backup (NAS-readonly).
        _snap(1, pid, "Backup", "/Volumes/Backup/RECOVERED-X/foo.txt"),
        # Non-recovered claim on L2 (live).
        _snap(2, pid, "L2", "/Volumes/Level 2/foo.txt"),
    ]
    m = reconcile_dedup_retire(claims=claims, policy=policy, steward_version="t")
    assert len(m.rows) == 1
    assert m.rows[0].action == "nas_manifest"
    assert m.rows[0].source_path == "/Volumes/Backup/RECOVERED-X/foo.txt"
