# SPDX-License-Identifier: Apache-2.0

"""Path → volume resolution for one host of an estate. Pure: no filesystem access.

Order: every mount ``match_regex`` (declaration order), then the longest matching
prefix among mount paths, explicit ``prefixes`` and volume ``aliases``. A path no
volume claims falls back to ``other-volume`` (under ``/Volumes/<name>/``) or
``unknown``, with access ``ignore``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from steward.core.errors import EstateError
from steward.core.estate.schema import LABEL_VOLUME_SEGMENT, Access, Estate, Mount, Volume

_VOLUME_SEGMENT_RE = re.compile(r"^/Volumes/([^/]+)/")


@dataclass(frozen=True, slots=True)
class VolumeMatch:
    """Classification of one path on one host."""

    volume_id: str | None
    tier: str
    label: str
    access: Access
    root: str | None = None
    """Directory the match is anchored at (mount path, or the alias it came through)."""


def _volume_segment(path: str) -> str:
    m = _VOLUME_SEGMENT_RE.match(path)
    return m.group(1) if m else ""


def _unmatched(path: str) -> VolumeMatch:
    if path.startswith("/Volumes/"):
        return VolumeMatch(None, "other-volume", _volume_segment(path), "ignore")
    return VolumeMatch(None, "unknown", "", "ignore")


def _default_label(vol: Volume, mount: Mount) -> str:
    if vol.label is not None:
        return vol.label
    return vol.tier if mount.path == "/" else mount.path.rsplit("/", 1)[1]


@dataclass(frozen=True, slots=True)
class _PrefixRule:
    prefix: str
    whole_dir: bool
    """Also match the directory itself (``prefix`` minus its trailing slash)."""
    match: VolumeMatch


class HostResolver:
    """Classifier for one host, compiled once and reused across many paths."""

    def __init__(self, estate: Estate, host: str) -> None:
        if host not in estate.hosts:
            raise EstateError(f"host {host!r} is not declared in the estate")
        self.host = host
        self._regex: list[tuple[re.Pattern[str], VolumeMatch]] = []
        prefixes: list[_PrefixRule] = []
        for vol_id, vol in estate.volumes.items():
            mount = vol.mounts.get(host)
            if mount is None:
                continue
            base = VolumeMatch(vol_id, vol.tier, _default_label(vol, mount), mount.access, mount.path)
            if mount.match_regex is not None:
                label = mount.regex_label if mount.regex_label is not None else base.label
                self._regex.append((re.compile(mount.match_regex), replace(base, label=label)))
            if mount.prefixes:
                for rule in mount.prefixes:
                    label = rule.label if rule.label is not None else base.label
                    prefixes.append(_PrefixRule(rule.prefix, False, replace(base, label=label)))
            else:
                prefixes.append(_PrefixRule(mount.path.rstrip("/") + "/", True, base))
            for alias in vol.aliases:
                prefixes.append(_PrefixRule(alias, False, replace(base, root=alias.rstrip("/") or "/")))
        # Stable sort: equal-length prefixes keep declaration order.
        self._prefixes = sorted(prefixes, key=lambda r: len(r.prefix), reverse=True)

    def classify(self, path: str) -> VolumeMatch:
        if not path:
            return _unmatched(path)
        for pattern, match in self._regex:
            if pattern.search(path):
                return self._finish(match, path)
        for rule in self._prefixes:
            if path.startswith(rule.prefix) or (rule.whole_dir and path == rule.prefix[:-1]):
                return self._finish(rule.match, path)
        return _unmatched(path)

    @staticmethod
    def _finish(match: VolumeMatch, path: str) -> VolumeMatch:
        if match.label == LABEL_VOLUME_SEGMENT:
            return replace(match, label=_volume_segment(path))
        return match


def classify(path: str, host: str, estate: Estate) -> VolumeMatch:
    """Classify ``path`` as seen from ``host``. Compile a :class:`HostResolver` for bulk use."""
    return HostResolver(estate, host).classify(path)


def relative_to_root(path: str, root: str) -> str | None:
    """``path`` relative to ``root`` (``""`` for the root itself), or ``None`` if outside it."""
    if path == root:
        return ""
    base = root if root.endswith("/") else root + "/"
    return path[len(base) :] if path.startswith(base) else None
