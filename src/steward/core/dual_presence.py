# SPDX-License-Identifier: Apache-2.0

"""Pure dual-presence classification (ADR-0020).

I/O-free types and path policy for store vs mount object presence.
Callers that need existence checks live in ``steward.infra.dual_presence``.

Conflict-named paths (Selective Sync Conflict segments) are never
``dual`` for cloud bulk filters — mapping is unreliable even if both
sides happen to exist under conflict renames.

Does **not** rewrite claim paths or invent dual-index rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from steward.core.fp_paths import (
    dropbox_mount_path,
    dropbox_mount_root,
    dropbox_relative,
    dropbox_store_path,
)

PresenceKind = Literal[
    "dual",
    "store_only",
    "mount_only",
    "missing_store",
    "conflict_name_path",
    "outside_store_root",
    "mount_error",
    "unknown",
]

DualPresenceIntent = Literal["cloud_retire", "local_reclaim", "observe"]

DEFAULT_CONFLICT_SUFFIX = " (Selective Sync Conflict)"

CLOUD_SAFE_KINDS: frozenset[PresenceKind] = frozenset({"dual"})
LOCAL_RECLAIM_KINDS: frozenset[PresenceKind] = frozenset({"dual", "store_only"})
ALL_PRESENCE_KINDS: tuple[PresenceKind, ...] = (
    "dual",
    "store_only",
    "mount_only",
    "missing_store",
    "conflict_name_path",
    "outside_store_root",
    "mount_error",
    "unknown",
)


@dataclass(frozen=True, slots=True)
class DualPresenceClass:
    """I/O-free mapping result for one claim/path."""

    kind: PresenceKind
    relative: str | None
    store_path: str | None
    mount_path: str | None
    notes: tuple[str, ...] = ()


def is_conflict_relative(
    relative: str | None,
    *,
    suffix: str = DEFAULT_CONFLICT_SUFFIX,
) -> bool:
    """True when any path segment contains the Selective Sync Conflict marker."""
    if not relative:
        return False
    # Match script + field notes: segment endswith strip or substring in part.
    stripped = suffix.strip()
    for part in relative.replace("\\", "/").split("/"):
        if not part:
            continue
        if suffix in part or (stripped and part.endswith(stripped)):
            return True
    return False


def classify_presence_kind(
    *,
    store_exists: bool | None,
    mount_exists: bool | None,
    relative: str | None,
    conflict_suffix: str = DEFAULT_CONFLICT_SUFFIX,
    store_error: bool = False,
    mount_error: bool = False,
    outside_store_root: bool = False,
) -> PresenceKind:
    """Pure matrix over existence / error / conflict flags.

    Precedence:
    1. outside_store_root
    2. conflict_name_path (never dual for cloud filters)
    3. mount_error (when mount side failed; do not promote to dual)
    4. existence combinations
    5. unknown when existence was not probed
    """
    if outside_store_root:
        return "outside_store_root"
    if is_conflict_relative(relative, suffix=conflict_suffix):
        return "conflict_name_path"
    if mount_error:
        return "mount_error"
    # store_error with unknown existence → treat store as missing/unreadable
    se = store_exists
    me = mount_exists
    if se is None and me is None and not store_error:
        return "unknown"
    if store_error and se is not True:
        se = False
    if se is True and me is True:
        return "dual"
    if se is True and me is False:
        return "store_only"
    if se is False and me is True:
        return "mount_only"
    if se is False and (me is False or me is None):
        return "missing_store"
    if se is None and me is True:
        return "mount_only"
    if se is True and me is None:
        # Mount not probed; cannot claim dual.
        return "unknown"
    if se is None and me is False:
        return "unknown"
    return "unknown"


def map_claim_to_pair(
    claim_path: str,
    *,
    store_root: str | None = None,
    mount_root: str | None = None,
) -> DualPresenceClass:
    """Map a claim path to store/mount forms without ``os.stat``.

    Uses :mod:`steward.core.fp_paths` Dropbox prefixes when roots are
    omitted. Explicit roots allow offline filter roots (script defaults /
    operator overrides).
    """
    notes: list[str] = []
    path = (claim_path or "").strip()
    if not path:
        return DualPresenceClass(
            kind="outside_store_root",
            relative=None,
            store_path=None,
            mount_path=None,
            notes=("empty claim path",),
        )

    # Explicit roots: relative_to store_root via string prefix.
    if store_root is not None:
        sroot = store_root.rstrip("/")
        mroot = (
            mount_root.rstrip("/")
            if mount_root is not None
            else dropbox_mount_root().rstrip("/")
        )
        if path == sroot or path.startswith(sroot + "/"):
            rel = path[len(sroot) :].lstrip("/")
        elif mount_root is not None and (
            path == mroot or path.startswith(mroot + "/")
        ):
            rel = path[len(mroot) :].lstrip("/")
            notes.append("claim path is mount form; remapped via explicit roots")
        else:
            # Fall back to built-in Dropbox mapping before declaring outside.
            rel_fb = dropbox_relative(path)
            if rel_fb is not None:
                rel = rel_fb
                notes.append("claim matched built-in Dropbox prefixes outside explicit store_root")
            else:
                return DualPresenceClass(
                    kind="outside_store_root",
                    relative=None,
                    store_path=path,
                    mount_path=None,
                    notes=("path not under store_root or Dropbox prefixes",),
                )
        store_p = f"{sroot}/{rel}" if rel else sroot
        mount_p = f"{mroot}/{rel}" if rel else mroot
        kind: PresenceKind = (
            "conflict_name_path"
            if is_conflict_relative(rel)
            else "unknown"  # existence not probed
        )
        return DualPresenceClass(
            kind=kind,
            relative=rel,
            store_path=store_p,
            mount_path=mount_p,
            notes=tuple(notes),
        )

    # Default: built-in Dropbox store/mount mapping.
    rel_db = dropbox_relative(path)
    if rel_db is None:
        return DualPresenceClass(
            kind="outside_store_root",
            relative=None,
            store_path=path,
            mount_path=None,
            notes=("not a Dropbox store or mount path",),
        )
    rel = rel_db
    store_p = dropbox_store_path(rel)
    mount_p = (
        dropbox_mount_path(rel)
        if mount_root is None
        else (f"{mount_root.rstrip('/')}/{rel}" if rel else mount_root.rstrip("/"))
    )
    kind2: PresenceKind = (
        "conflict_name_path" if is_conflict_relative(rel) else "unknown"
    )
    return DualPresenceClass(
        kind=kind2,
        relative=rel,
        store_path=store_p,
        mount_path=mount_p,
        notes=tuple(notes),
    )


def kinds_for_intent(intent: DualPresenceIntent) -> frozenset[PresenceKind] | None:
    """Return execute-safe kinds for *intent*, or None for observe (all)."""
    if intent == "cloud_retire":
        return CLOUD_SAFE_KINDS
    if intent == "local_reclaim":
        return LOCAL_RECLAIM_KINDS
    return None


def cloud_safe_ratio(
    *,
    dual: int,
    store_only: int,
) -> float | None:
    """``dual / (dual + store_only)`` among those two buckets; None if empty."""
    denom = dual + store_only
    if denom <= 0:
        return None
    return dual / float(denom)


__all__ = [
    "ALL_PRESENCE_KINDS",
    "CLOUD_SAFE_KINDS",
    "DEFAULT_CONFLICT_SUFFIX",
    "LOCAL_RECLAIM_KINDS",
    "DualPresenceClass",
    "DualPresenceIntent",
    "PresenceKind",
    "classify_presence_kind",
    "cloud_safe_ratio",
    "is_conflict_relative",
    "kinds_for_intent",
    "map_claim_to_pair",
]
