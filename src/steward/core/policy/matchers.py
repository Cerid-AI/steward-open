# SPDX-License-Identifier: Apache-2.0

"""Path/string matchers used by the policy evaluator.

Pure functions — no I/O, no globals. The matchers operate on already-
extracted attributes (path, basename, domain, …) so they're cheap to
re-run on every claim during plan generation.
"""
from __future__ import annotations

from collections.abc import Iterable
from os.path import basename


def matches_any_substring(path: str, substrings: Iterable[str]) -> bool:
    """Return True if any substring appears in ``path``. Case-sensitive."""
    return any(s in path for s in substrings)


def matches_any_basename_prefix(path: str, prefixes: Iterable[str]) -> bool:
    """Return True if the file's basename starts with any of ``prefixes``."""
    base = basename(path)
    return any(base.startswith(p) for p in prefixes)


def matches_basename_exact(path: str, names: Iterable[str]) -> bool:
    """Return True if the file's basename is exactly one of ``names``."""
    return basename(path) in set(names)


def is_noise(
    path: str,
    *,
    always_skip_substrings: Iterable[str] = (),
    basename_prefixes: Iterable[str] = (),
    basename_exact: Iterable[str] = (),
) -> bool:
    """Convenience composite: True iff path matches any noise rule.

    Mirrors :func:`steward.infra.importer.legacy_unified._is_noise` /
    :mod:`steward.infra.scanner.skiplist` semantics — but driven by a
    pydantic-validated policy rather than hard-coded constants.
    """
    return (
        matches_any_substring(path, always_skip_substrings)
        or matches_any_basename_prefix(path, basename_prefixes)
        or matches_basename_exact(path, basename_exact)
    )
