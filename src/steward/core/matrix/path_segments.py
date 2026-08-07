# SPDX-License-Identifier: Apache-2.0
"""Pure path prefix / next-segment helpers for inventory surface."""

from __future__ import annotations


def normalize_prefix(prefix: str) -> str:
    if not prefix:
        return ""
    return prefix.replace("\\", "/").rstrip("/")


def next_segment(file_path: str, prefix: str) -> str | None:
    """First path component under prefix when remainder is a nested path.

    Returns None when the file is a direct child of prefix (leaf) or
    when the path is outside the prefix.
    """
    path = file_path.replace("\\", "/")
    pref = normalize_prefix(prefix)
    if pref:
        if path == pref:
            return None
        if not path.startswith(pref + "/"):
            return None
        rest = path[len(pref) + 1 :]
    else:
        rest = path.lstrip("/")
    if not rest:
        return None
    if "/" not in rest:
        return None
    return rest.split("/", 1)[0]


def leaf_name_under_prefix(file_path: str, prefix: str) -> str | None:
    """Basename when file is a direct child of prefix; else None."""
    path = file_path.replace("\\", "/")
    pref = normalize_prefix(prefix)
    if pref:
        if not path.startswith(pref + "/"):
            return None
        rest = path[len(pref) + 1 :]
    else:
        rest = path.lstrip("/")
    if rest and "/" not in rest:
        return rest
    return None


def child_path(prefix: str, name: str) -> str:
    pref = normalize_prefix(prefix)
    if not pref:
        return name if name.startswith("/") else f"/{name}" if name else ""
    return f"{pref}/{name}"
