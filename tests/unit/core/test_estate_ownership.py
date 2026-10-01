# SPDX-License-Identifier: Apache-2.0

"""Tests for :mod:`steward.core.estate.ownership`."""

from __future__ import annotations

from typing import Any

import pytest

from steward.core.estate import ACTIONS, Action, Estate, OwnershipPolicy, decide, default_estate
from steward.core.estate.legacy import LEGACY_HOST, LIVE_TIERS, NAS_READONLY_TIERS
from steward.core.estate.ownership import MUTATING_ACTIONS

# ── action × access matrix ────────────────────────────────────────────────────

# Host "me" sees one volume per access level. rw is only legal for the owner;
# "own-ro" is a volume "me" owns but mounts read-only.
_MATRIX_VOLUMES: dict[str, tuple[str, str]] = {
    "rw": ("me", "rw"),
    "ro": ("other", "ro"),
    "probe": ("other", "probe"),
    "ignore": ("other", "ignore"),
    "forbid": ("other", "forbid"),
    "own-ro": ("me", "ro"),
}

_COLUMNS = ("rw", "ro", "probe", "ignore", "forbid", "own-ro")
_MUTATE = (True, False, False, False, False, False)
# The spec table, one row per action, columns as _COLUMNS. Every volume is plan: full.
_EXPECTED: dict[Action, tuple[bool, ...]] = {
    "scan": (True, True, False, False, False, True),
    "plan": (True, False, False, False, False, True),
    "apply": _MUTATE,
    "stash": _MUTATE,
    "retire": _MUTATE,
    "promote-destination": _MUTATE,
    "stash-finalize": _MUTATE,
    "stash-restore": _MUTATE,
    "nas_manifest": (False,) * 6,  # needs plan nas-manifest
    "promote-source": (True, True, False, False, False, True),
    "replicate-destination": _MUTATE,  # no grants: owner + rw only
    "archive-destination": _MUTATE,
    "probe": (True, True, True, False, True, True),
}


def _matrix_estate(enforcement: str = "enforce") -> Estate:
    volumes: dict[str, Any] = {}
    for name, (owner, access) in _MATRIX_VOLUMES.items():
        mounts: dict[str, Any] = {"me": {"path": f"/Volumes/{name}", "access": access}}
        if owner == "other":
            mounts["other"] = {"path": f"/Volumes/{name}", "access": "rw"}
        volumes[f"v-{name}"] = {"tier": "Data", "owner": owner, "mounts": mounts}
    return Estate.model_validate(
        {
            "version": 1,
            "enforcement": enforcement,
            "hosts": {"me": {"role": "primary"}, "other": {"role": "client"}},
            "volumes": volumes,
        }
    )


def test_matrix_covers_every_action() -> None:
    assert set(_EXPECTED) == set(ACTIONS)


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("column", _COLUMNS)
def test_action_access_matrix(action: Action, column: str) -> None:
    expected = _EXPECTED[action][_COLUMNS.index(column)]
    d = decide(action, f"/Volumes/{column}/sub/file", "me", _matrix_estate())
    assert d.allowed is expected, d.reason
    assert d.volume_id == f"v-{column}"
    assert d.access == _MATRIX_VOLUMES[column][1]


def test_unmanaged_path_is_refused_for_everything() -> None:
    policy = OwnershipPolicy(_matrix_estate(), "me")
    for action in ACTIONS:
        d = policy.decide(action, "/Volumes/Unlisted/x")
        assert not d.allowed and d.volume_id is None, action
        assert "not on any volume" in d.reason


def test_unknown_action_rejected() -> None:
    with pytest.raises(ValueError, match="unknown ownership action"):
        decide("delete", "/Volumes/rw/x", "me", _matrix_estate())  # type: ignore[arg-type]


# ── plan modes ────────────────────────────────────────────────────────────────


def _plan_estate(plan: str, *, scan: bool = True) -> Estate:
    return Estate.model_validate(
        {
            "version": 1,
            "hosts": {"me": {"role": "primary"}},
            "volumes": {
                "v": {
                    "tier": "Data",
                    "owner": "me",
                    "plan": plan,
                    "scan": scan,
                    "mounts": {"me": {"path": "/V", "access": "rw"}},
                }
            },
        }
    )


_PLAN_ALLOWS: dict[str, set[str]] = {
    "full": set(MUTATING_ACTIONS - {"nas_manifest"}) | {"promote-source", "plan"},
    "nas-manifest": {"nas_manifest", "promote-source", "plan"},
    "source-only": {"promote-source", "plan"},
    "none": set(),
}


@pytest.mark.parametrize("plan", sorted(_PLAN_ALLOWS))
def test_plan_mode_gates_planning_actions(plan: str) -> None:
    policy = OwnershipPolicy(_plan_estate(plan), "me")
    gated = MUTATING_ACTIONS | {"promote-source", "plan"}
    allowed = {a for a in gated if policy.decide(a, "/V/x").allowed}
    assert allowed == _PLAN_ALLOWS[plan]


def test_source_only_without_scan_allows_promote_source_only() -> None:
    policy = OwnershipPolicy(_plan_estate("source-only", scan=False), "me")
    assert policy.decide("promote-source", "/V/x").allowed
    for action in ("retire", "stash", "nas_manifest", "apply", "promote-destination", "scan"):
        assert not policy.decide(action, "/V/x").allowed, action


# ── report vs enforce ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mode", "blocks", "would_refuse"),
    [("enforce", True, False), ("report", False, True)],
)
def test_enforcement_mode_on_refusal(mode: str, blocks: bool, would_refuse: bool) -> None:
    d = decide("retire", "/Volumes/ro/x", "me", _matrix_estate(mode))
    assert not d.allowed and d.mode == mode
    assert (d.blocks, d.would_refuse) == (blocks, would_refuse)


@pytest.mark.parametrize("mode", ["enforce", "report"])
def test_allowed_never_blocks_or_reports(mode: str) -> None:
    d = decide("retire", "/Volumes/rw/x", "me", _matrix_estate(mode))
    assert d.allowed and not d.blocks and not d.would_refuse


# ── forbid ────────────────────────────────────────────────────────────────────


def test_forbid_probe_is_a_finding(two_host_estate: Estate) -> None:
    d = decide("probe", "/Volumes/Studio Photos", "mac-pro", two_host_estate)
    assert d.allowed and d.finding and d.access == "forbid"
    owner = decide("probe", "/Volumes/Studio Photos", "studio", two_host_estate)
    assert owner.allowed and not owner.finding


@pytest.mark.parametrize("action", sorted(set(ACTIONS) - {"probe"}))
def test_forbid_refuses_everything_else(two_host_estate: Estate, action: Action) -> None:
    assert not decide(action, "/Volumes/Studio Photos/x", "mac-pro", two_host_estate).allowed


# ── the two-host estate ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("action", "path", "studio", "mac_pro"),
    [
        ("scan", "/Volumes/Level_3a/x", True, False),
        ("retire", "/Volumes/Level_3a/x", True, False),
        ("stash", "/Volumes/Level_3a/x", True, False),
        ("promote-source", "/Volumes/Level_3a/x", True, True),
        ("scan", "/Volumes/Backup/x", False, False),
        ("promote-source", "/Volumes/Backup/x", True, True),
        ("promote-source", "/Volumes/NFS-Backup/x", True, True),
        ("retire", "/Volumes/Backup/x", False, False),
        ("stash", "/Volumes/Backup/x", False, False),
        ("nas_manifest", "/Volumes/Backup/x", False, False),
        ("plan", "/Volumes/Backup/x", True, False),
        ("plan", "/Volumes/Cache/x", False, False),
        ("scan", "/Volumes/Cache/x", True, False),
        ("retire", "/Volumes/Cache/x", False, False),
        ("scan", "/Volumes/operator/Develop/x", False, False),
        ("probe", "/Volumes/operator", False, False),
        ("retire", "/Users/operator/Library/CloudStorage/Dropbox/a", True, True),
        ("stash", "/Volumes/Level 2/x", False, True),
        ("plan", "/Volumes/BOOTCAMP/x", False, False),
    ],
)
def test_two_host_decisions(two_host_estate: Estate, action: Action, path: str, studio: bool, mac_pro: bool) -> None:
    assert decide(action, path, "studio", two_host_estate).allowed is studio
    assert decide(action, path, "mac-pro", two_host_estate).allowed is mac_pro


# ── replica grants ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("action", ["replicate-destination", "archive-destination"])
@pytest.mark.parametrize(
    ("path", "studio", "mac_pro"),
    [
        ("/Volumes/Backup/_steward-mirror/studio/inventory.db", True, False),
        ("/Volumes/Backup/_steward-mirror/studio", True, False),
        ("/Volumes/Backup/_steward-archive/studio/repo", True, False),
        ("/Volumes/Backup/_steward-mirror/mac-pro/inventory.db", False, True),
        ("/Volumes/Backup/_steward-mirror/mac-pro", False, True),
        ("/Volumes/NFS-Backup/_steward-mirror/mac-pro/x", False, True),
        ("/Volumes/Backup/_steward-mirror/mac-pro-evil/x", False, False),
        ("/Volumes/Backup/_steward-mirror/studio2/x", False, False),
        ("/Volumes/Backup/_steward-mirror", False, False),
        ("/Volumes/Backup/x", False, False),
        ("/Volumes/Backup", False, False),
        ("/Volumes/Backup/Some-Mac.sparsebundle/bands/0", False, False),
    ],
)
def test_replica_grant_prefix_rules(
    two_host_estate: Estate, action: Action, path: str, studio: bool, mac_pro: bool
) -> None:
    assert decide(action, path, "studio", two_host_estate).allowed is studio
    assert decide(action, path, "mac-pro", two_host_estate).allowed is mac_pro


def test_owner_destination_without_grants(two_host_estate: Estate) -> None:
    assert decide("replicate-destination", "/Volumes/Level_3a/_mirror/x", "studio", two_host_estate).allowed
    assert not decide("replicate-destination", "/Volumes/Level_3a/_mirror/x", "mac-pro", two_host_estate).allowed


def test_remote_destination_is_outside_the_estate(two_host_estate: Estate) -> None:
    d = decide("archive-destination", "b2:bucket/steward", "mac-pro", two_host_estate)
    assert d.allowed and d.volume_id is None


# ── legacy estate never refuses what 0.3.27 does ──────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/Users/operator/x",
        "/private/var/x",
        "/Volumes/Level 1/x",
        "/Volumes/Level 1w/x",
        "/Volumes/Level 2/x",
        "/Volumes/Level_3a/x",
        "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/x",
        "/Users/operator/Library/CloudStorage/Dropbox/x",
    ],
)
def test_legacy_live_tiers_allow_every_mutation(path: str) -> None:
    policy = OwnershipPolicy(default_estate(), LEGACY_HOST)
    for action in sorted(set(ACTIONS) - {"nas_manifest"}):
        d = policy.decide(action, path)
        assert d.allowed, (action, d.reason)


def test_legacy_backup_is_nas_manifest_only() -> None:
    policy = OwnershipPolicy(default_estate(), LEGACY_HOST)
    assert NAS_READONLY_TIERS == {"Backup"}
    for path in ("/Volumes/Backup/x", "/Volumes/NFS-Backup/x"):
        assert policy.decide("nas_manifest", path).allowed
        assert policy.decide("promote-source", path).allowed
        assert policy.decide("scan", path).allowed
        assert policy.decide("replicate-destination", path).allowed
        assert not policy.decide("stash", path).allowed
        assert not policy.decide("retire", path).allowed


def test_legacy_mode_is_report() -> None:
    estate = default_estate()
    assert estate.enforcement == "report"
    assert {estate.volumes[v].tier for v in estate.volumes if estate.volumes[v].plan == "full"} >= LIVE_TIERS
