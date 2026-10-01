# SPDX-License-Identifier: Apache-2.0

"""Tier classification — pure function from path to tier identifier.

Ported verbatim from ``sprawl-audit/scripts/unified_hash_db.py::classify_tier``;
the grammar now lives in the legacy estate (:mod:`steward.core.estate.legacy`),
which is what Steward uses when no estate file is configured.

Tier semantics (Steward v0.1, macOS-first):

* ``boot``       — system / user dirs under ``/Users`` or ``/private`` or ``/var``
* ``L1``         — Level 1 SSD (``/Volumes/Level 1``)
* ``L1w``        — Level 1 working SSD (``/Volumes/Level 1w``)
* ``L2``         — Level 2 HDD (``/Volumes/Level 2``)
* ``L3a``        — NAS NFS (``/Volumes/Level_3a`` or legacy ``/Volumes/NFS-Level3a``)
* ``Backup``     — NAS NFS read-only (``/Volumes/Backup`` or legacy ``/Volumes/NFS-Backup``)
* ``DropboxStorage`` — cloud-synced (``/Volumes/DropboxStorage``)
* ``BOOTCAMP``   — Windows partition (``/Volumes/BOOTCAMP``)
* ``other-volume`` — any other ``/Volumes/*`` mount (catch-all)
* ``unknown``    — empty or unrecognized path
"""

from __future__ import annotations

from functools import cache

from steward.core.estate.legacy import (
    CLOUD_FP_COOLING_OFF,
    CLOUD_FP_TIERS,
    LEGACY_HOST,
    LIVE_TIERS,
    NAS_READONLY_TIERS,
    TIER_PRIORITY,
    default_estate,
)
from steward.core.estate.resolve import HostResolver

__all__ = [
    "CLOUD_FP_COOLING_OFF",
    "CLOUD_FP_TIERS",
    "LIVE_TIERS",
    "NAS_READONLY_TIERS",
    "TIER_PRIORITY",
    "classify_tier",
]


@cache
def _legacy_resolver() -> HostResolver:
    return HostResolver(default_estate(), LEGACY_HOST)


def classify_tier(path: str) -> tuple[str, str]:
    """Return ``(tier, volume_top_level)`` for ``path``.

    Pure function — no I/O, no filesystem access. Matches a path-prefix
    grammar; mount-options aren't consulted because Steward operates over
    paths as they appear in claims, not as live mounts.
    """
    match = _legacy_resolver().classify(path)
    return (match.tier, match.label)
