# SPDX-License-Identifier: Apache-2.0

"""Tests for :mod:`steward.core.estate.schema` and the open policy ``Tier`` type."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from steward.core.estate import Estate, PrefixRule, default_estate
from steward.core.policy.schema import DedupRetire, PromotionDefaults


def _invalid(raw: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        Estate.model_validate(raw)


def test_two_host_estate_validates(two_host_estate: Estate) -> None:
    assert set(two_host_estate.hosts) == {"studio", "mac-pro"}
    backup = two_host_estate.volumes["nas-backup"]
    assert (backup.plan, backup.scan, backup.time_machine_target) == ("source-only", False, True)
    boot = two_host_estate.volumes["macpro-boot"].mounts["mac-pro"]
    assert boot.prefixes == (PrefixRule(prefix="/Users/"), PrefixRule(prefix="/private/"), PrefixRule(prefix="/var/"))
    assert two_host_estate.hosts["mac-pro"].publish is not None
    assert two_host_estate.hosts["mac-pro"].publish.max_age_hours == 36


def test_legacy_estate_validates() -> None:
    estate = default_estate()
    assert list(estate.hosts) == ["local"]
    assert all(v.owner == "local" for v in estate.volumes.values())
    assert estate is default_estate()


def test_rw_only_for_owner(two_host_raw: dict[str, Any]) -> None:
    two_host_raw["volumes"]["nas-l3a"]["mounts"]["mac-pro"]["access"] = "rw"
    _invalid(two_host_raw, "only the owner .* may mount rw")


def test_owner_must_be_declared(two_host_raw: dict[str, Any]) -> None:
    two_host_raw["volumes"]["studio-work"]["owner"] = "laptop"
    _invalid(two_host_raw, "owner 'laptop' is not a declared host")


def test_mount_host_must_be_declared(two_host_raw: dict[str, Any]) -> None:
    two_host_raw["volumes"]["studio-work"]["mounts"]["laptop"] = {"path": "/Volumes/Work", "access": "ro"}
    _invalid(two_host_raw, "mount host 'laptop' is not a declared host")


@pytest.mark.parametrize("host_id", ["Studio", "mac_pro", "mac pro", "", "studio.local"])
def test_host_id_regex(two_host_raw: dict[str, Any], host_id: str) -> None:
    two_host_raw["hosts"][host_id] = {"role": "client"}
    _invalid(two_host_raw, "host id .* must match")


def test_volume_id_regex(two_host_raw: dict[str, Any]) -> None:
    two_host_raw["volumes"]["Studio_Work"] = two_host_raw["volumes"].pop("studio-work")
    _invalid(two_host_raw, "volume id .* must match")


def test_duplicate_mount_path_per_host(two_host_raw: dict[str, Any]) -> None:
    two_host_raw["volumes"]["studio-scratch"] = {
        "tier": "Scratch",
        "owner": "studio",
        "mounts": {"studio": {"path": "/Volumes/Work", "access": "rw"}},
    }
    _invalid(two_host_raw, "host 'studio' already mounts '/Volumes/Work' for volume 'studio-work'")


def test_same_mount_path_on_different_hosts_is_fine(two_host_estate: Estate) -> None:
    l3a = two_host_estate.volumes["nas-l3a"].mounts
    assert l3a["studio"].path == l3a["mac-pro"].path == "/Volumes/Level_3a"


def test_nested_mount_paths_are_fine(two_host_raw: dict[str, Any]) -> None:
    two_host_raw["volumes"]["studio-work-scratch"] = {
        "tier": "Scratch",
        "owner": "studio",
        "mounts": {"studio": {"path": "/Volumes/Work/scratch", "access": "rw"}},
    }
    Estate.model_validate(two_host_raw)


def test_time_machine_grant_must_be_root_slash_host(two_host_raw: dict[str, Any]) -> None:
    grants = two_host_raw["volumes"]["nas-backup"]["replica_grants"]
    grants.append({"host": "mac-pro", "prefix": "_steward-mirror/studio"})
    _invalid(two_host_raw, "Time Machine target grants must be '<root>/mac-pro'")


@pytest.mark.parametrize("prefix", ["mac-pro", "_steward-mirror/mac-pro/db", "a/b/mac-pro"])
def test_time_machine_grant_shape(two_host_raw: dict[str, Any], prefix: str) -> None:
    two_host_raw["volumes"]["nas-backup"]["replica_grants"] = [{"host": "mac-pro", "prefix": prefix}]
    _invalid(two_host_raw, "Time Machine target grants")


def test_non_time_machine_grant_namespace_is_free(two_host_raw: dict[str, Any]) -> None:
    two_host_raw["volumes"]["nas-l3a"]["replica_grants"] = [{"host": "mac-pro", "prefix": "inbox/from-macpro"}]
    Estate.model_validate(two_host_raw)


@pytest.mark.parametrize(
    "prefix", ["/abs/mac-pro", "_steward-mirror/../mac-pro", "a//mac-pro", "_steward-mirror/mac-pro/"]
)
def test_grant_prefix_must_be_clean_relative(two_host_raw: dict[str, Any], prefix: str) -> None:
    two_host_raw["volumes"]["nas-l3a"]["replica_grants"] = [{"host": "mac-pro", "prefix": prefix}]
    _invalid(two_host_raw, "replica grant prefix")


def test_grant_host_must_be_declared(two_host_raw: dict[str, Any]) -> None:
    two_host_raw["volumes"]["nas-l3a"]["replica_grants"] = [{"host": "laptop", "prefix": "x/laptop"}]
    _invalid(two_host_raw, "replica grant host 'laptop'")


def test_source_only_may_skip_scanning(two_host_estate: Estate) -> None:
    backup = two_host_estate.volumes["nas-backup"]
    assert backup.plan == "source-only" and backup.scan is False


@pytest.mark.parametrize("plan", ["full", "nas-manifest"])
def test_planned_volume_requires_scan(two_host_raw: dict[str, Any], plan: str) -> None:
    two_host_raw["volumes"]["nas-backup"]["plan"] = plan
    _invalid(two_host_raw, f"plan '{plan}' requires scan: true")


def test_plan_none_may_skip_scanning(two_host_raw: dict[str, Any]) -> None:
    two_host_raw["volumes"]["studio-cache"]["scan"] = False
    Estate.model_validate(two_host_raw)


def test_cloud_fp_kind_and_block_go_together(two_host_raw: dict[str, Any]) -> None:
    del two_host_raw["volumes"]["macpro-dropbox"]["cloud_fp"]
    _invalid(two_host_raw, "cloud_fp is required")


def test_exclusive_attach_needs_image(two_host_raw: dict[str, Any]) -> None:
    del two_host_raw["volumes"]["studio-photos"]["image"]
    _invalid(two_host_raw, "exclusive_attach requires image")


def test_exclusive_attach_host_declared(two_host_raw: dict[str, Any]) -> None:
    two_host_raw["volumes"]["studio-photos"]["exclusive_attach"] = "laptop"
    _invalid(two_host_raw, "exclusive_attach 'laptop'")


def test_mount_regex_must_compile(two_host_raw: dict[str, Any]) -> None:
    two_host_raw["volumes"]["macpro-dropbox"]["mounts"]["mac-pro"]["match_regex"] = "/Dropbox(["
    _invalid(two_host_raw, "does not compile")


@pytest.mark.parametrize(
    ("field", "value"),
    [("path", "Volumes/Work"), ("path", "/Volumes/Work/"), ("prefixes", ["Users/"])],
)
def test_mount_paths_are_absolute(two_host_raw: dict[str, Any], field: str, value: Any) -> None:
    two_host_raw["volumes"]["studio-work"]["mounts"]["studio"][field] = value
    _invalid(two_host_raw, "absolute path|must not end with")


def test_unknown_fields_rejected(two_host_raw: dict[str, Any]) -> None:
    two_host_raw["volumes"]["studio-work"]["mounts"]["studio"]["acess"] = "rw"
    _invalid(two_host_raw, "acess")


def test_unknown_access_rejected(two_host_raw: dict[str, Any]) -> None:
    two_host_raw["volumes"]["studio-work"]["mounts"]["studio"]["access"] = "write"
    _invalid(two_host_raw, "access")


def test_estate_is_frozen(two_host_estate: Estate) -> None:
    with pytest.raises(ValidationError):
        two_host_estate.enforcement = "enforce"  # type: ignore[misc]


# ── open policy Tier ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("tier", ["Work", "container", "StudioPhotos", "other-volume", "L1w", "Backup"])
def test_policy_tier_accepts_estate_tiers(tier: str) -> None:
    PromotionDefaults(source_tier=tier)
    DedupRetire(tier_priority={tier: 0}, live_tiers=[tier], stash_roots={tier: "/x"}, nas_manifest_tiers=[tier])


@pytest.mark.parametrize("tier", ["", " boot", "boot ", "/Volumes/Work", "../L1", "1L", "L 1", "boot\n", "x" * 65])
def test_policy_tier_rejects_garbage(tier: str) -> None:
    with pytest.raises(ValidationError, match="invalid tier name"):
        PromotionDefaults(source_tier=tier)
    with pytest.raises(ValidationError, match="invalid tier name"):
        DedupRetire(tier_priority={tier: 0}, live_tiers=[])


def test_estate_volume_tier_uses_policy_tier(two_host_raw: dict[str, Any]) -> None:
    two_host_raw["volumes"]["studio-work"]["tier"] = "Work Disk"
    _invalid(two_host_raw, "invalid tier name")
