# SPDX-License-Identifier: Apache-2.0

"""Preservation: cloud-FP retire path laws (ADR-0014 / 0015)."""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.core.fp_paths import resolve_fp_paths
from steward.core.policy.reconciler import ClaimSnapshot, reconcile_dedup_retire
from steward.core.policy.schema import (
    DedupRetire,
    RetentionExclusions,
    RetentionPolicy,
)


@pytest.mark.preservation
def test_resolve_verify_equals_unlink_always(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    claim = "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/x.bin"
    for prefer in (True, False):
        r = resolve_fp_paths(claim, prefer_mount_unlink=prefer)
        assert r.verify_path == r.unlink_path, prefer


@pytest.mark.preservation
def test_reconciler_dropbox_is_retire_direct_not_stash() -> None:
    policy = RetentionPolicy(
        version=1,
        kind="RetentionPolicy",
        exclusions=RetentionExclusions(),
        dedup_retire=DedupRetire(
            tier_priority={"L1": 1, "DropboxStorage": 5},
            live_tiers=["L1", "DropboxStorage"],
            cooling_off_days=7,
            stash_roots={},
            nas_manifest_tiers=[],
        ),
    )
    claims = [
        ClaimSnapshot(
            claim_id=1,
            permanode_id="a" * 32,
            canonical_hash="0" * 64,
            machine_id="m",
            file_path="/Volumes/Level 1/keep.bin",
            tier="L1",
            size_bytes=10,
        ),
        ClaimSnapshot(
            claim_id=2,
            permanode_id="a" * 32,
            canonical_hash="0" * 64,
            machine_id="m",
            file_path="/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/dup.bin",
            tier="DropboxStorage",
            size_bytes=10,
        ),
    ]
    m = reconcile_dedup_retire(claims=claims, policy=policy, steward_version="test")
    assert len(m.rows) == 1
    assert m.rows[0].action == "retire_direct"
