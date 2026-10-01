# SPDX-License-Identifier: Apache-2.0

"""Which estate host this machine is, and whether the open DB belongs to it.

``$STEWARD_HOST`` names the host id directly. Otherwise the system hostname,
minus a trailing ``.local``, is matched case-insensitively against every
host's ``hostnames``. With an estate present and no match the host is
*unknown*: read commands warn, mutating commands refuse.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import Literal

from steward.core.errors import BindingMismatchError, UnknownHostError
from steward.core.estate import Estate, Host
from steward.infra.estate.loader import current

HOST_ENV = "STEWARD_HOST"

HostSource = Literal["env", "hostname"]
BindingStatus = Literal["bound", "unpinned", "missing", "mismatch"]


def _strip_local(name: str) -> str:
    return name[: -len(".local")] if name.lower().endswith(".local") else name


def system_hostname() -> str:
    return _strip_local(socket.gethostname())


@dataclass(frozen=True, slots=True)
class HostIdentity:
    host_id: str | None
    """Estate host id, or ``None`` when this machine is not in the estate."""
    source: HostSource
    hostname: str
    """``$STEWARD_HOST`` or the system hostname that was matched."""
    reason: str

    @property
    def known(self) -> bool:
        return self.host_id is not None


def resolve_host(estate: Estate, *, hostname: str | None = None) -> HostIdentity:
    override = os.getenv(HOST_ENV)
    if override:
        if override in estate.hosts:
            return HostIdentity(override, "env", override, f"{HOST_ENV}={override}")
        declared = ", ".join(sorted(estate.hosts))
        return HostIdentity(
            None, "env", override, f"{HOST_ENV}={override} is not a declared host (declared: {declared})"
        )
    name = _strip_local(hostname) if hostname is not None else system_hostname()
    wanted = name.casefold()
    matches = [
        host_id
        for host_id, host in estate.hosts.items()
        if any(_strip_local(h).casefold() == wanted for h in host.hostnames)
    ]
    if len(matches) == 1:
        return HostIdentity(matches[0], "hostname", name, f"hostname {name!r}")
    if matches:
        return HostIdentity(
            None, "hostname", name, f"hostname {name!r} matches several hosts: {', '.join(sorted(matches))}"
        )
    return HostIdentity(
        None,
        "hostname",
        name,
        f"hostname {name!r} matches no host in the estate; set {HOST_ENV} or add it to hostnames",
    )


def current_identity() -> HostIdentity | None:
    """This machine's identity in the configured estate; ``None`` without an estate (legacy)."""
    estate = current()
    return None if estate is None else resolve_host(estate)


def require_known_host(identity: HostIdentity) -> str:
    """The host id, or :class:`UnknownHostError` — the gate for mutating commands."""
    if identity.host_id is None:
        raise UnknownHostError(f"refusing to mutate: this machine is not an estate host ({identity.reason})")
    return identity.host_id


@dataclass(frozen=True, slots=True)
class Binding:
    """Whether the inventory.db is the one the estate pins for this host."""

    status: BindingStatus
    expected: str | None
    actual: str | None

    @property
    def ok(self) -> bool:
        return self.status != "mismatch"

    def describe(self) -> str:
        if self.status == "bound":
            return f"bound to machine_id {self.actual}"
        if self.status == "unpinned":
            actual = self.actual or "no inventory.db yet"
            return f"estate pins no machine_id (DB: {actual})"
        if self.status == "missing":
            return f"estate pins machine_id {self.expected} but there is no inventory.db"
        return f"inventory.db machine_id {self.actual} is not the pinned {self.expected}"


def check_binding(host: Host, db_machine_id: str | None) -> Binding:
    expected = host.machine_id
    if expected is None:
        return Binding("unpinned", None, db_machine_id)
    if db_machine_id is None:
        return Binding("missing", expected, None)
    if db_machine_id != expected:
        return Binding("mismatch", expected, db_machine_id)
    return Binding("bound", expected, db_machine_id)


def require_binding(binding: Binding) -> None:
    """Raise :class:`BindingMismatchError` when mutating would touch another machine's DB."""
    if binding.status == "mismatch":
        raise BindingMismatchError(f"refusing to mutate: {binding.describe()}")
