# SPDX-License-Identifier: Apache-2.0

"""Cloud File Provider (FP) store ↔ user-facing mount path mapping (ADR-0015).

macOS File Providers expose content under two path families:

1. **Store path** — the on-disk materialization the FP agent owns
   (e.g. ``/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/...``).
   Stats and reads here are usually reliable; deletes here may **not**
   propagate to the cloud (field notes 2026-07-13 gap #1).
2. **Mount path** — the user-facing virtual mount
   (e.g. ``~/Library/CloudStorage/Dropbox/...``). Deletes here are
   recognized as user deletes and typically land in cloud trash.

``retire_direct`` prefers the mount path for **both** verify and
``unlink()`` (verify==unlink) so cloud-propagating deletes stay
consistent. Scan claims may still record store or mount form; this
module normalizes between them. External-drive FP (store on
``/Volumes/DropboxStorage``, mount under CloudStorage) is a normal
layout — different devices alone are not a mapping bug.

Pure functions only — no I/O. Callers that need existence checks do
that at the infra boundary.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# Dropbox FP — store materialization roots (longest-prefix first).
_DROPBOX_STORE_PREFIXES: tuple[str, ...] = (
    "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox/",
    "/Volumes/DropboxStorage/Dropbox/",  # symlink into the store root
)

# Relative segment after a store prefix (no leading slash).
_DROPBOX_MOUNT_REL_ROOT = "Library/CloudStorage/Dropbox"


@dataclass(frozen=True)
class FPPathResolution:
    """Result of resolving a claim path for cloud-FP retire.

    Attributes:
        claim_path: Original path from the manifest / claims table.
        unlink_path: Path that should receive ``Path.unlink()`` for
            cloud-propagating deletes (mount when mappable).
        verify_path: Path preferred for existence / size / hash checks
            (store when known — more reliable under congestion).
        tier_hint: ``"DropboxStorage"`` / ``"iCloudDrive"`` / ``None``.
        used_mount_for_unlink: True when unlink_path is a mount path.
        store_path: Store form if known, else None.
        mount_path: Mount form if known, else None.
    """

    claim_path: str
    unlink_path: str
    verify_path: str
    tier_hint: str | None
    used_mount_for_unlink: bool
    store_path: str | None
    mount_path: str | None


def _home() -> Path:
    """Return home directory; overridable via HOME for tests."""
    return Path(os.environ.get("HOME", str(Path.home()))).expanduser()


def dropbox_mount_root() -> str:
    """Absolute mount root for Dropbox FP (trailing slash)."""
    return str(_home() / _DROPBOX_MOUNT_REL_ROOT) + "/"


def dropbox_relative(path: str) -> str | None:
    """Return the path relative to the Dropbox root, or None if not Dropbox."""
    if not path:
        return None
    # Normalize double slashes lightly; keep absolute.
    p = path
    mount = dropbox_mount_root()
    if p.startswith(mount):
        return p[len(mount) :]
    if p == mount.rstrip("/"):
        return ""
    for prefix in _DROPBOX_STORE_PREFIXES:
        if p.startswith(prefix):
            return p[len(prefix) :]
        if p == prefix.rstrip("/"):
            return ""
    return None


def dropbox_store_path(relative: str) -> str:
    """Build the canonical store path for a Dropbox-relative path."""
    rel = relative.lstrip("/")
    base = _DROPBOX_STORE_PREFIXES[0].rstrip("/")
    return f"{base}/{rel}" if rel else base


def dropbox_mount_path(relative: str) -> str:
    """Build the user-facing mount path for a Dropbox-relative path."""
    rel = relative.lstrip("/")
    base = dropbox_mount_root().rstrip("/")
    return f"{base}/{rel}" if rel else base


def is_dropbox_path(path: str) -> bool:
    return dropbox_relative(path) is not None


def is_icloud_mount_path(path: str) -> bool:
    """True for user-facing iCloud Drive mount paths."""
    if not path:
        return False
    # Mobile Documents is the standard iCloud Drive mount prefix.
    icloud = str(_home() / "Library/Mobile Documents")
    return path == icloud or path.startswith(icloud + "/")


def resolve_fp_paths(
    claim_path: str,
    *,
    prefer_mount_unlink: bool = True,
) -> FPPathResolution:
    """Resolve claim_path into verify + unlink targets for FP retire.

    **Invariant (logic law):** ``verify_path == unlink_path`` always.
    Never hash-check path A and delete path B — on this host store and
    mount can be forked materializations (experiment 2026-07-28).

    When ``prefer_mount_unlink`` is True (default, ADR-0015) and the
    path maps to Dropbox, **both** verify and unlink use the user-facing
    mount form (cloud-propagating delete).

    When False, both use the claim path as written (local reclaim —
    ``apply --allow-store-path-unlink``).

    Non-FP paths pass through unchanged.
    """
    rel = dropbox_relative(claim_path)
    if rel is not None:
        store = dropbox_store_path(rel)
        mount = dropbox_mount_path(rel)
        if prefer_mount_unlink:
            # Same path for verify + unlink (mount).
            return FPPathResolution(
                claim_path=claim_path,
                unlink_path=mount,
                verify_path=mount,
                tier_hint="DropboxStorage",
                used_mount_for_unlink=True,
                store_path=store,
                mount_path=mount,
            )
        # Local reclaim: operate only on the claim path as recorded.
        return FPPathResolution(
            claim_path=claim_path,
            unlink_path=claim_path,
            verify_path=claim_path,
            tier_hint="DropboxStorage",
            used_mount_for_unlink=False,
            store_path=store,
            mount_path=mount,
        )

    if is_icloud_mount_path(claim_path):
        # iCloud store materialization is opaque; mount path is both
        # verify and unlink target.
        return FPPathResolution(
            claim_path=claim_path,
            unlink_path=claim_path,
            verify_path=claim_path,
            tier_hint="iCloudDrive",
            used_mount_for_unlink=True,
            store_path=None,
            mount_path=claim_path,
        )

    return FPPathResolution(
        claim_path=claim_path,
        unlink_path=claim_path,
        verify_path=claim_path,
        tier_hint=None,
        used_mount_for_unlink=False,
        store_path=None,
        mount_path=None,
    )


def claim_path_aliases(claim_path: str) -> tuple[str, ...]:
    """Return path forms that may appear in ``claims.file_path`` for one file.

    Used when flipping ``is_current`` after a retire so both store and
    mount claim rows (if both were scanned) are marked non-current.
    """
    aliases: list[str] = [claim_path]
    rel = dropbox_relative(claim_path)
    if rel is not None:
        for candidate in (
            dropbox_store_path(rel),
            dropbox_mount_path(rel),
            # Symlink form under the volume root.
            f"/Volumes/DropboxStorage/Dropbox/{rel}" if rel else "/Volumes/DropboxStorage/Dropbox",
        ):
            if candidate not in aliases:
                aliases.append(candidate)
    return tuple(aliases)


def posix_parent(path: str) -> str:
    """Parent of a posix path string (no I/O)."""
    return str(PurePosixPath(path).parent)


__all__ = [
    "FPPathResolution",
    "claim_path_aliases",
    "dropbox_mount_path",
    "dropbox_mount_root",
    "dropbox_relative",
    "dropbox_store_path",
    "is_dropbox_path",
    "is_icloud_mount_path",
    "posix_parent",
    "resolve_fp_paths",
]
