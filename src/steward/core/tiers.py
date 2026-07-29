# SPDX-License-Identifier: Apache-2.0

"""Tier classification — pure function from path to tier identifier.

Ported verbatim from ``sprawl-audit/scripts/unified_hash_db.py::classify_tier``.
The tier registry table (``tiers``) is populated from the same data in M4.

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

import re

_OTHER_VOLUME_RE = re.compile(r"^/Volumes/([^/]+)/")

# Dropbox File Provider user-facing mount under CloudStorage (any home).
# Must be matched *before* the /Users boot rule. Includes multi-account
# suffixes such as Dropbox-Personal.
_CLOUDSTORAGE_DROPBOX_RE = re.compile(r"/Library/CloudStorage/Dropbox(?:-[^/]+)?(?:/|$)")


def classify_tier(path: str) -> tuple[str, str]:
    """Return ``(tier, volume_top_level)`` for ``path``.

    Pure function — no I/O, no filesystem access. Matches a path-prefix
    grammar; mount-options aren't consulted because Steward operates over
    paths as they appear in claims, not as live mounts.
    """
    if not path:
        return ("unknown", "")
    # Dropbox CloudStorage mount (ADR-0015) — before /Users → boot.
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
