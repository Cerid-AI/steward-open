# SPDX-License-Identifier: Apache-2.0

"""Ownership decisions: may this host perform this action on this path?

Pure. The verdict (``allowed``) is the same in both enforcement modes; the mode
says what the caller does with a refusal — ``report`` audits it and proceeds,
``enforce`` refuses (:attr:`Decision.blocks`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from steward.core.estate.resolve import HostResolver, VolumeMatch, relative_to_root
from steward.core.estate.schema import Access, Enforcement, Estate, PlanMode

Action = Literal[
    "scan",
    "plan",
    "apply",
    "stash",
    "retire",
    "promote-destination",
    "stash-finalize",
    "stash-restore",
    "nas_manifest",
    "promote-source",
    "replicate-destination",
    "archive-destination",
    "probe",
]
ACTIONS: tuple[Action, ...] = get_args(Action)

MUTATING_ACTIONS: frozenset[Action] = frozenset(
    {"apply", "stash", "retire", "promote-destination", "stash-finalize", "stash-restore", "nas_manifest"}
)
"""Owner-only actions that need ``rw`` access and a plan mode that permits them."""

DESTINATION_ACTIONS: frozenset[Action] = frozenset({"replicate-destination", "archive-destination"})

PLAN_PERMITS: dict[PlanMode, frozenset[Action]] = {
    "full": frozenset(MUTATING_ACTIONS - {"nas_manifest"}) | {"promote-source"},
    "nas-manifest": frozenset({"nas_manifest", "promote-source"}),
    "source-only": frozenset({"promote-source"}),
    "none": frozenset(),
}

_READABLE: frozenset[Access] = frozenset({"rw", "ro"})


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str
    mode: Enforcement
    action: Action
    volume_id: str | None
    access: Access
    finding: bool = False
    """``probe`` on a ``forbid`` mount: probing is allowed, and presence is a health failure."""

    @property
    def blocks(self) -> bool:
        """Refuse the action (enforce mode)."""
        return not self.allowed and self.mode == "enforce"

    @property
    def would_refuse(self) -> bool:
        """Audit ``ownership_would_refuse`` and proceed (report mode)."""
        return not self.allowed and self.mode == "report"


class OwnershipPolicy:
    """Decisions for one host, with the resolver compiled once."""

    def __init__(self, estate: Estate, host: str) -> None:
        self.estate = estate
        self.host = host
        self.resolver = HostResolver(estate, host)

    def decide(self, action: Action, path: str) -> Decision:
        if action not in ACTIONS:
            raise ValueError(f"unknown ownership action {action!r}")
        if action in DESTINATION_ACTIONS and not path.startswith("/"):
            return self._out(True, f"{action} to a remote ({path!r}) is outside the estate", action, None, "ignore")
        match = self.resolver.classify(path)
        if match.volume_id is None:
            return self._out(False, f"{path!r} is not on any volume declared for host {self.host!r}", action, match)
        vol = self.estate.volumes[match.volume_id]
        mount = vol.mounts[self.host]
        access = match.access
        owner = vol.owner == self.host
        where = f"volume {match.volume_id!r} ({access} on {self.host!r}, owner {vol.owner!r}, plan {vol.plan!r})"

        if action == "probe":
            if access == "ignore":
                return self._out(False, f"{where}: ignored on this host", action, match)
            if access == "forbid":
                return self._out(True, f"{where}: must not be present on this host", action, match, finding=True)
            return self._out(True, f"{where}: probe", action, match)

        if action == "scan":
            if access not in _READABLE:
                return self._out(False, f"{where}: scanning needs rw or ro access", action, match)
            if not vol.scan:
                return self._out(False, f"{where}: volume is scan: false", action, match)
            if not mount.scan:
                return self._out(False, f"{where}: mount is scan: false on this host", action, match)
            return self._out(True, f"{where}: scan", action, match)

        if action == "promote-source":
            if access not in _READABLE:
                return self._out(False, f"{where}: reading needs rw or ro access", action, match)
            if action not in PLAN_PERMITS[vol.plan]:
                return self._out(False, f"{where}: plan does not allow promote sources", action, match)
            return self._out(True, f"{where}: promote source", action, match)

        if action == "plan":
            if not owner:
                return self._out(False, f"{where}: only the owner plans this volume", action, match)
            if vol.plan == "none":
                return self._out(False, f"{where}: plan none", action, match)
            return self._out(True, f"{where}: plannable", action, match)

        if action in DESTINATION_ACTIONS:
            return self._destination(action, path, match, where)

        if not owner:
            return self._out(False, f"{where}: only the owner may {action}", action, match)
        if access != "rw":
            return self._out(False, f"{where}: {action} needs rw access", action, match)
        if action not in PLAN_PERMITS[vol.plan]:
            return self._out(False, f"{where}: plan {vol.plan!r} does not allow {action}", action, match)
        return self._out(True, f"{where}: {action}", action, match)

    def _destination(self, action: Action, path: str, match: VolumeMatch, where: str) -> Decision:
        vol = self.estate.volumes[str(match.volume_id)]
        if match.access not in _READABLE:
            return self._out(False, f"{where}: {action} needs rw or ro access", action, match)
        rel = relative_to_root(path, match.root) if match.root is not None else None
        grants = [g for g in vol.replica_grants if g.host == self.host]
        for grant in grants:
            if rel is not None and (rel == grant.prefix or rel.startswith(grant.prefix + "/")):
                return self._out(True, f"{where}: replica grant {grant.prefix!r}", action, match)
        if vol.replica_grants:
            granted = ", ".join(repr(g.prefix) for g in grants) or "none"
            return self._out(False, f"{where}: outside this host's replica grants ({granted})", action, match)
        if vol.owner == self.host and match.access == "rw":
            return self._out(True, f"{where}: owner destination", action, match)
        return self._out(False, f"{where}: no replica grant for this host", action, match)

    def _out(
        self,
        allowed: bool,
        reason: str,
        action: Action,
        match: VolumeMatch | None,
        access: Access | None = None,
        *,
        finding: bool = False,
    ) -> Decision:
        return Decision(
            allowed=allowed,
            reason=reason,
            mode=self.estate.enforcement,
            action=action,
            volume_id=match.volume_id if match else None,
            access=access if access is not None else (match.access if match else "ignore"),
            finding=finding,
        )


def decide(action: Action, path: str, host: str, estate: Estate) -> Decision:
    """One-shot decision; build an :class:`OwnershipPolicy` for bulk use."""
    return OwnershipPolicy(estate, host).decide(action, path)
