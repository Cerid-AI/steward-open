# SPDX-License-Identifier: Apache-2.0

"""Noise-path filter — directories and files Steward never inventories.

Ported from ``sprawl-audit/scripts/nas_hash_walk.py``. The substrings
mirror :data:`steward.infra.importer.legacy_unified._NOISE_SUBSTRINGS`
plus the macOS / Synology system metadata. The M4 retention policy YAML
overrides this at apply-time; this list is the *scanner-level* hard
filter — paths that should never become claims at all. On top of it, a
volume in the estate file may list its own ``scan_excludes``
(:func:`scan_excludes_for`).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass

from steward.infra.estate.active import active_estate

# Directory names — match anywhere in the path tree. ``os.scandir`` skips
# matching dirs entirely (no recursion).
DEFAULT_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".fseventsd",
        ".Spotlight-V100",
        ".Trashes",
        ".TemporaryItems",
        ".DocumentRevisions-V100",
        ".PKInstallSandboxManager",
        "@eaDir",
        "@SynoResource",
    }
)

# Disk-image bundles (Time Machine, Photos libraries) are walked as plain
# directories otherwise: their band files become claims, and identical bands
# across images look like duplicates — stashing one corrupts the image.
SKIP_DIR_SUFFIXES: tuple[str, ...] = (".sparsebundle",)

# File-name prefixes — AppleDouble files etc.
SKIP_FILE_PREFIXES: tuple[str, ...] = ("._",)

# File-name exact matches.
SKIP_FILE_EXACT: frozenset[str] = frozenset({".DS_Store", ".apdisk", ".localized"})


def is_skipped_dir(name: str) -> bool:
    """Return True iff a directory name matches the skip set or a skipped suffix."""
    return name in DEFAULT_SKIP_DIRS or name.lower().endswith(SKIP_DIR_SUFFIXES)


def is_skipped_file(name: str) -> bool:
    """Return True iff a file basename matches a skip prefix or exact rule."""
    if name in SKIP_FILE_EXACT:
        return True
    return any(name.startswith(p) for p in SKIP_FILE_PREFIXES)


def filter_dirs(names: Iterable[str]) -> list[str]:
    """Return the input dir names with skipped ones removed (preserves order)."""
    return [n for n in names if not is_skipped_dir(n)]


def filter_files(names: Iterable[str]) -> list[str]:
    """Return the input file names with skipped ones removed (preserves order)."""
    return [n for n in names if not is_skipped_file(n)]


@dataclass(frozen=True, slots=True)
class ScanExcludes:
    """A volume's ``scan_excludes`` globs, anchored at the volume root.

    gitignore-style: a trailing ``/`` matches directories only; a pattern with
    no other ``/`` matches the basename at any depth; one with a ``/`` matches
    the path relative to ``anchor``.
    """

    anchor: str
    patterns: tuple[str, ...] = ()

    def excludes(self, path: str, *, is_dir: bool) -> bool:
        if not self.patterns:
            return False
        base = self.anchor.rstrip("/") + "/"
        rel = path[len(base) :] if path.startswith(base) else None
        name = path.rsplit("/", 1)[-1]
        for raw in self.patterns:
            if raw.endswith("/") and not is_dir:
                continue
            pattern = raw.rstrip("/")
            if "/" in pattern:
                if rel is not None and fnmatch.fnmatchcase(rel, pattern.lstrip("/")):
                    return True
            elif fnmatch.fnmatchcase(name, pattern):
                return True
        return False


NO_EXCLUDES = ScanExcludes(anchor="/")


def scan_excludes_for(root: str) -> ScanExcludes:
    """The ``scan_excludes`` of the estate volume ``root`` lies on (none without an estate file)."""
    ctx = active_estate()
    if ctx.legacy:
        return NO_EXCLUDES
    match = ctx.resolver.classify(root)
    if match.volume_id is None or match.root is None:
        return NO_EXCLUDES
    patterns = ctx.estate.volumes[match.volume_id].scan_excludes
    return ScanExcludes(anchor=match.root, patterns=patterns) if patterns else NO_EXCLUDES
