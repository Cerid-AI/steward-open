# SPDX-License-Identifier: Apache-2.0

"""The estate Steward assumes when no estate file exists.

One implicit host (:data:`LEGACY_HOST`) owns every volume with ``rw`` access,
and the mounts transcribe the single-host prefix grammar of
:func:`steward.core.tiers.classify_tier` exactly, raw ``startswith`` prefixes
included (``/Volumes/Level 1w`` and ``/Volumes/DropboxStorage`` carry no
trailing slash). Plan modes are permissive — the retention policy's
``live_tiers`` still decides what is planned — except ``Backup``, which stays
NAS-manifest only (:data:`NAS_READONLY_TIERS`).
"""

from __future__ import annotations

from functools import cache

from steward.core.estate.schema import (
    LABEL_VOLUME_SEGMENT,
    CloudFP,
    Estate,
    Host,
    Mount,
    PrefixRule,
    Volume,
)

LEGACY_HOST = "local"

CLOUDSTORAGE_DROPBOX_REGEX = r"/Library/CloudStorage/Dropbox(?:-[^/]+)?(?:/|$)"
"""Dropbox File Provider user-facing mount under CloudStorage (any home), including
multi-account suffixes such as ``Dropbox-Personal``. Matched before the ``/Users`` boot rule."""

# Tier priority ladder — lower is more canonical. Used by the
# dedup-retire reconciler to decide which copy to keep when N copies of
# the same permanode exist across tiers. Wired into ``retention.yml`` in M4.
TIER_PRIORITY: dict[str, int] = {
    "boot": 0,
    "L1": 1,
    "L1w": 2,
    "L2": 3,
    "L3a": 4,
    "DropboxStorage": 5,
    "Backup": 6,
    "BOOTCAMP": 7,
    "other-volume": 8,
    "unknown": 99,
}


LIVE_TIERS: frozenset[str] = frozenset({"boot", "L1", "L1w", "L2", "L3a", "DropboxStorage"})
"""Tiers eligible for live-side mutate in retention plans.

Most use same-FS ``stash`` rename. Members of :data:`CLOUD_FP_TIERS`
use ``retire_direct`` instead (ADR-0014) — never stash-rename.
"""

CLOUD_FP_TIERS: frozenset[str] = frozenset({"DropboxStorage"})
"""Cloud File Provider tiers — external trash is the cooling-off.

Reconciler emits ``retire_direct`` (not ``stash``). Default cooling-off
mechanism labels live in :data:`CLOUD_FP_COOLING_OFF`.
"""

CLOUD_FP_COOLING_OFF: dict[str, str] = {
    "DropboxStorage": "dropbox-cloud-trash-account-specific",
}
"""Default ``destination_tier`` / cooling-off mechanism string for FP retires.

Account-specific windows (30 d base vs Extended Version History) — do not
hardcode a day count into the label.
"""

NAS_READONLY_TIERS: frozenset[str] = frozenset({"Backup"})
"""Tiers Steward never writes directly; mutations emit NAS manifests for DSM/SSH execution."""

LEGACY_DROPBOX_FP = CloudFP(
    provider="dropbox",
    store_root="/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox",
    store_aliases=("/Volumes/DropboxStorage/Dropbox",),
    mount_root="~/Library/CloudStorage/Dropbox",
    cooling_off=CLOUD_FP_COOLING_OFF["DropboxStorage"],
)
"""The Dropbox File Provider layout assumed without an estate file (ADR-0015)."""


def _rw(path: str, *prefixes: PrefixRule | str, **kw: object) -> dict[str, Mount]:
    rules = tuple(p if isinstance(p, PrefixRule) else PrefixRule(prefix=p) for p in prefixes)
    return {LEGACY_HOST: Mount.model_validate({"path": path, "prefixes": rules, "access": "rw", **kw})}


@cache
def default_estate() -> Estate:
    """The legacy single-host estate (built once; models are frozen)."""
    return Estate(
        version=1,
        metadata={"name": "legacy"},
        hosts={LEGACY_HOST: Host(role="primary")},
        volumes={
            "boot": Volume(
                kind="boot",
                tier="boot",
                owner=LEGACY_HOST,
                mounts=_rw(
                    "/",
                    PrefixRule(prefix="/Users/", label="boot-Users"),
                    PrefixRule(prefix="/private/", label="boot-system"),
                    PrefixRule(prefix="/var/", label="boot-system"),
                ),
            ),
            "l1w": Volume(
                tier="L1w", label="Level_1w", owner=LEGACY_HOST, mounts=_rw("/Volumes/Level 1w", "/Volumes/Level 1w")
            ),
            "l1": Volume(
                tier="L1", label="Level_1", owner=LEGACY_HOST, mounts=_rw("/Volumes/Level 1", "/Volumes/Level 1/")
            ),
            "l2": Volume(
                tier="L2", label="Level_2", owner=LEGACY_HOST, mounts=_rw("/Volumes/Level 2", "/Volumes/Level 2/")
            ),
            "l3a": Volume(
                kind="nas",
                tier="L3a",
                label="Level_3a",
                owner=LEGACY_HOST,
                aliases=("/Volumes/NFS-Level3a/",),
                mounts=_rw("/Volumes/Level_3a", "/Volumes/Level_3a/"),
            ),
            "backup": Volume(
                kind="nas",
                tier="Backup",
                label="Backup",
                owner=LEGACY_HOST,
                plan="nas-manifest",
                aliases=("/Volumes/NFS-Backup/",),
                mounts=_rw("/Volumes/Backup", "/Volumes/Backup/"),
            ),
            "dropbox": Volume(
                kind="cloud-fp",
                tier="DropboxStorage",
                label="DropboxStorage",
                owner=LEGACY_HOST,
                cloud_fp=LEGACY_DROPBOX_FP,
                mounts=_rw(
                    "/Volumes/DropboxStorage",
                    "/Volumes/DropboxStorage",
                    match_regex=CLOUDSTORAGE_DROPBOX_REGEX,
                    regex_label="Dropbox_CloudStorage",
                    criticality="critical",
                ),
            ),
            "bootcamp": Volume(
                tier="BOOTCAMP",
                label="BOOTCAMP",
                owner=LEGACY_HOST,
                mounts=_rw("/Volumes/BOOTCAMP", "/Volumes/BOOTCAMP/"),
            ),
            "other-volume": Volume(
                tier="other-volume",
                label=LABEL_VOLUME_SEGMENT,
                owner=LEGACY_HOST,
                mounts=_rw("/Volumes", "/Volumes/"),
            ),
        },
    )
