# SPDX-License-Identifier: Apache-2.0

"""The estate as this process sees it: the configured file + this host, or the legacy estate.

Classification, probes and cloud File Provider paths all go through here, so a
machine with no estate file keeps the single-host behaviour exactly. On a
machine the estate does not list, classification falls back to the legacy
grammar too (with a warning): its mounts are unknown, and mutation is refused
elsewhere (:func:`steward.infra.estate.host.require_known_host`).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import cache

from steward.core.estate import LEGACY_HOST, CloudFP, Estate, HostResolver, VolumeMatch, default_estate
from steward.core.estate.legacy import LEGACY_DROPBOX_FP
from steward.core.fp_paths import expand_mount_root
from steward.infra.estate.host import HOST_ENV, HostIdentity, resolve_host
from steward.infra.estate.loader import current

logger = logging.getLogger("steward.infra.estate.active")


@dataclass(frozen=True, slots=True)
class ActiveEstate:
    estate: Estate
    host_id: str
    resolver: HostResolver
    legacy: bool
    """True without an estate file, or when this machine is not one of its hosts."""
    identity: HostIdentity | None = None
    """This machine's identity in the configured estate; ``None`` without one."""

    @property
    def configured(self) -> bool:
        """An estate file exists (whether or not it lists this host)."""
        return self.identity is not None


@cache
def _legacy() -> ActiveEstate:
    estate = default_estate()
    return ActiveEstate(estate, LEGACY_HOST, HostResolver(estate, LEGACY_HOST), legacy=True)


_cache: tuple[Estate, str | None, ActiveEstate] | None = None


def active_estate() -> ActiveEstate:
    """The estate and host to classify against (cached per loaded estate and ``$STEWARD_HOST``)."""
    global _cache
    estate = current()
    if estate is None:
        return _legacy()
    override = os.getenv(HOST_ENV)
    if _cache is not None and _cache[0] is estate and _cache[1] == override:
        return _cache[2]
    identity = resolve_host(estate)
    if identity.host_id is None:
        logger.warning("estate does not list this machine (%s); using the legacy tier grammar", identity.reason)
        legacy = _legacy()
        ctx = ActiveEstate(legacy.estate, legacy.host_id, legacy.resolver, legacy=True, identity=identity)
    else:
        ctx = ActiveEstate(estate, identity.host_id, HostResolver(estate, identity.host_id), False, identity)
    _cache = (estate, override, ctx)
    return ctx


def classify(path: str) -> VolumeMatch:
    return active_estate().resolver.classify(path)


def classify_tier(path: str) -> tuple[str, str]:
    """``(tier, label)`` for ``path`` on this host — drop-in for :func:`steward.core.tiers.classify_tier`."""
    match = active_estate().resolver.classify(path)
    return (match.tier, match.label)


@dataclass(frozen=True, slots=True)
class CloudFPLayout:
    """A cloud File Provider volume as mounted on this host."""

    volume_id: str
    tier: str
    fp: CloudFP
    volume_root: str
    """Where the volume holding the store is mounted (e.g. ``/Volumes/DropboxStorage``)."""

    @property
    def store_root(self) -> str:
        return self.fp.store_root

    @property
    def mount_root(self) -> str:
        return expand_mount_root(self.fp)


def _legacy_cloud_fp() -> CloudFPLayout:
    vol = default_estate().volumes["dropbox"]
    return CloudFPLayout("dropbox", vol.tier, LEGACY_DROPBOX_FP, vol.mounts[LEGACY_HOST].path)


def cloud_fp_layout() -> CloudFPLayout | None:
    """This host's cloud File Provider volume; ``None`` when the estate gives it none.

    The first cloud-fp volume this host mounts (``ignore``/``forbid`` mounts do
    not count). Legacy mode always has the Dropbox layout.
    """
    ctx = active_estate()
    if ctx.legacy:
        return _legacy_cloud_fp()
    for vol_id, vol in ctx.estate.volumes.items():
        mount = vol.mounts.get(ctx.host_id)
        if vol.cloud_fp is None or mount is None or mount.access in ("ignore", "forbid"):
            continue
        return CloudFPLayout(vol_id, vol.tier, vol.cloud_fp, mount.path)
    return None


def cloud_fp_or_default() -> CloudFPLayout:
    """:func:`cloud_fp_layout`, or the legacy Dropbox layout for explicit FP commands on hosts without one."""
    return cloud_fp_layout() or _legacy_cloud_fp()


__all__ = [
    "ActiveEstate",
    "CloudFPLayout",
    "active_estate",
    "classify",
    "classify_tier",
    "cloud_fp_layout",
    "cloud_fp_or_default",
]
