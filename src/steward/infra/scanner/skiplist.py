# SPDX-License-Identifier: Apache-2.0

"""Noise-path filter — directories and files Steward never inventories.

Ported from ``sprawl-audit/scripts/nas_hash_walk.py``. The substrings
mirror :data:`steward.infra.importer.legacy_unified._NOISE_SUBSTRINGS`
plus the macOS / Synology system metadata. The M4 retention policy YAML
overrides this at apply-time; this list is the *scanner-level* hard
filter — paths that should never become claims at all.
"""
from __future__ import annotations

from collections.abc import Iterable

# Directory names — match anywhere in the path tree. ``os.scandir`` skips
# matching dirs entirely (no recursion).
DEFAULT_SKIP_DIRS: frozenset[str] = frozenset({
    ".fseventsd",
    ".Spotlight-V100",
    ".Trashes",
    ".TemporaryItems",
    ".DocumentRevisions-V100",
    ".PKInstallSandboxManager",
    "@eaDir",
    "@SynoResource",
})

# File-name prefixes — AppleDouble files etc.
SKIP_FILE_PREFIXES: tuple[str, ...] = ("._",)

# File-name exact matches.
SKIP_FILE_EXACT: frozenset[str] = frozenset({".DS_Store", ".apdisk", ".localized"})


def is_skipped_dir(name: str) -> bool:
    """Return True iff a directory name matches the skip set."""
    return name in DEFAULT_SKIP_DIRS


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
