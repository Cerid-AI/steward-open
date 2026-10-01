# SPDX-License-Identifier: Apache-2.0

"""Pydantic schema for an estate: the hosts Steward runs on and the volumes they share.

Two hosts can mount the same share at the same path, so a path alone cannot say
who may act on it. Every volume therefore names one ``owner`` host, and each host
that sees the volume gets its own mount entry with an :data:`Access` level.

Pure shape + cross-reference validation. Path resolution lives in
:mod:`steward.core.estate.resolve`; action decisions in
:mod:`steward.core.estate.ownership`.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from steward.core.policy.schema import Tier

ID_RE = re.compile(r"[a-z0-9-]+")
"""Host and volume ids (``fullmatch``)."""

LABEL_VOLUME_SEGMENT = "{volume}"
"""Label placeholder: the ``/Volumes/<name>/`` segment of the classified path
(empty when the path has no trailing component), as the legacy catch-all labels
``other-volume`` claims."""

Access = Literal["rw", "ro", "probe", "ignore", "forbid"]
"""What a host may do on a mounted volume.

* ``rw`` — mutate (owner only)
* ``ro`` — read, scan, probe
* ``probe`` — capacity probes only
* ``ignore`` — known and deliberately unmanaged
* ``forbid`` — must not be present on this host; presence is a health failure
"""

Role = Literal["primary", "client"]
VolumeKind = Literal["boot", "local", "nas", "cloud-fp", "disk-image"]
PlanMode = Literal["full", "nas-manifest", "source-only", "none"]
"""What plans may emit for a volume.

* ``full`` — stash / retire / promote-destination rows
* ``nas-manifest`` — NAS manifests only; may also be read as a promote source
* ``source-only`` — may be read as a promote source; nothing is ever planned onto it
* ``none`` — observe only
"""
Criticality = Literal["critical", "normal"]
Enforcement = Literal["report", "enforce"]


def _check_absolute(value: str, what: str) -> str:
    if not value.startswith("/"):
        raise ValueError(f"{what} must be an absolute path: {value!r}")
    return value


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PrefixRule(_Model):
    """A raw path-prefix (``str.startswith``) that classifies into a mount's volume."""

    prefix: str
    label: str | None = None
    """Claim label for paths under this prefix; defaults to the volume's label."""

    @model_validator(mode="after")
    def _absolute(self) -> PrefixRule:
        _check_absolute(self.prefix, "prefix")
        return self


def _coerce_prefix(value: Any) -> Any:
    return {"prefix": value} if isinstance(value, str) else value


class Mount(_Model):
    """How one host sees one volume."""

    path: str
    """Mount point on this host. With no ``prefixes``, the volume claims this path and everything below it."""

    prefixes: tuple[Annotated[PrefixRule, BeforeValidator(_coerce_prefix)], ...] = ()
    """Raw prefixes that replace ``path`` for classification (e.g. ``/Users/`` on a boot volume)."""

    match_regex: str | None = None
    """``re.search`` pattern checked before any prefix (e.g. a File Provider mount under ``/Users``)."""

    regex_label: str | None = None
    access: Access
    criticality: Criticality = "normal"
    scan: bool = True

    @model_validator(mode="after")
    def _check(self) -> Mount:
        _check_absolute(self.path, "mount path")
        if self.path != "/" and self.path.endswith("/"):
            raise ValueError(f"mount path must not end with '/': {self.path!r}")
        if self.match_regex is not None:
            try:
                re.compile(self.match_regex)
            except re.error as exc:
                raise ValueError(f"match_regex {self.match_regex!r} does not compile: {exc}") from exc
        if self.regex_label is not None and self.match_regex is None:
            raise ValueError("regex_label requires match_regex")
        return self


class CloudFP(_Model):
    """Cloud File Provider layout (ADR-0015): on-disk store vs user-facing mount."""

    provider: str
    store_root: str
    store_aliases: tuple[str, ...] = ()
    mount_root: str
    """Absolute, or ``~/``-relative to the running user's home."""
    cooling_off: str

    @model_validator(mode="after")
    def _paths(self) -> CloudFP:
        _check_absolute(self.store_root, "store_root")
        for alias in self.store_aliases:
            _check_absolute(alias, "store alias")
        if not self.mount_root.startswith("~/"):
            _check_absolute(self.mount_root, "mount_root")
        return self


class ReplicaGrant(_Model):
    """Lets ``host`` write replicas/archives under ``<mount>/<prefix>`` of a volume it does not own."""

    host: str
    prefix: str
    """Relative to the volume root, e.g. ``_steward-mirror/<host>``."""

    @model_validator(mode="after")
    def _relative(self) -> ReplicaGrant:
        parts = self.prefix.split("/")
        if self.prefix.startswith("/") or any(p in ("", ".", "..") for p in parts):
            raise ValueError(f"replica grant prefix must be a relative path without '.', '..' or '//': {self.prefix!r}")
        return self

    @property
    def segments(self) -> tuple[str, ...]:
        return tuple(self.prefix.split("/"))


class Volume(_Model):
    kind: VolumeKind = "local"
    tier: Tier
    label: str | None = None
    """Claim label (``claims.volume``). Defaults to the matched mount's last path segment, or the tier for ``/``."""

    owner: str
    scan: bool = True
    plan: PlanMode = "full"
    time_machine_target: bool = False
    aliases: tuple[str, ...] = ()
    """Legacy prefixes that also classify here, on any host that mounts the volume."""

    scan_excludes: tuple[str, ...] = ()
    cloud_fp: CloudFP | None = None
    image: str | None = None
    exclusive_attach: str | None = None
    replica_grants: tuple[ReplicaGrant, ...] = ()
    probe_skip: tuple[str, ...] = ()
    mounts: dict[str, Mount] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Volume:
        for alias in self.aliases:
            _check_absolute(alias, "alias")
        for skip in self.probe_skip:
            _check_absolute(skip, "probe_skip")
        if (self.kind == "cloud-fp") != (self.cloud_fp is not None):
            raise ValueError("cloud_fp is required for, and only allowed on, kind cloud-fp")
        if self.exclusive_attach is not None and self.image is None:
            raise ValueError("exclusive_attach requires image")
        if self.plan in ("full", "nas-manifest") and not self.scan:
            raise ValueError(f"plan {self.plan!r} requires scan: true (only source-only or none may skip scanning)")
        return self


class HostPublish(_Model):
    """Where a client host publishes its read-only inventory export for the primary to pull."""

    ssh: str
    envelope: str
    health_dir: str | None = None
    max_age_hours: float = Field(default=36.0, gt=0)


class Host(_Model):
    role: Role
    hostnames: tuple[str, ...] = ()
    machine_id: str | None = None
    data_dir: str | None = None
    publish: HostPublish | None = None

    @model_validator(mode="after")
    def _data_dir(self) -> Host:
        if self.data_dir is not None:
            _check_absolute(self.data_dir, "data_dir")
        return self


class Estate(_Model):
    version: Literal[1]
    kind: Literal["Estate"] = "Estate"
    metadata: dict[str, str] | None = None
    enforcement: Enforcement = "report"
    hosts: dict[str, Host] = Field(min_length=1)
    volumes: dict[str, Volume]

    @model_validator(mode="after")
    def _cross_references(self) -> Estate:
        for host_id in self.hosts:
            if not ID_RE.fullmatch(host_id):
                raise ValueError(f"host id {host_id!r} must match {ID_RE.pattern}")
        mount_paths: dict[tuple[str, str], str] = {}
        for vol_id, vol in self.volumes.items():
            if not ID_RE.fullmatch(vol_id):
                raise ValueError(f"volume id {vol_id!r} must match {ID_RE.pattern}")
            where = f"volume {vol_id!r}"
            if vol.owner not in self.hosts:
                raise ValueError(f"{where}: owner {vol.owner!r} is not a declared host")
            if vol.exclusive_attach is not None and vol.exclusive_attach not in self.hosts:
                raise ValueError(f"{where}: exclusive_attach {vol.exclusive_attach!r} is not a declared host")
            for host_id, mount in vol.mounts.items():
                if host_id not in self.hosts:
                    raise ValueError(f"{where}: mount host {host_id!r} is not a declared host")
                if mount.access == "rw" and host_id != vol.owner:
                    raise ValueError(f"{where}: only the owner ({vol.owner!r}) may mount rw, not {host_id!r}")
                key = (host_id, mount.path)
                if key in mount_paths:
                    raise ValueError(
                        f"{where}: host {host_id!r} already mounts {mount.path!r} for volume {mount_paths[key]!r}"
                    )
                mount_paths[key] = vol_id
            for grant in vol.replica_grants:
                if grant.host not in self.hosts:
                    raise ValueError(f"{where}: replica grant host {grant.host!r} is not a declared host")
                if vol.time_machine_target and (len(grant.segments) != 2 or grant.segments[1] != grant.host):
                    raise ValueError(
                        f"{where}: Time Machine target grants must be '<root>/{grant.host}', got {grant.prefix!r}"
                    )
        return self
