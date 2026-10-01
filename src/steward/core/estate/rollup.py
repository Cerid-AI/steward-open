# SPDX-License-Identifier: Apache-2.0

"""Which host speaks for which claims when several hosts' inventories are summed.

Two hosts that mount the same share both hold claims for it; only the
owner's are counted in an estate-wide rollup. Claims carry a tier, not a
volume id, so a tier is foreign to a host when every volume with that tier
is owned by some other host — a tier the host owns any volume of (``boot``
on every machine, say) is always its own.
"""

from __future__ import annotations

from steward.core.estate.schema import Estate


def host_for_machine(estate: Estate, machine_id: str) -> str | None:
    """The host whose pinned ``machine_id`` this is, if any."""
    for host_id, host in estate.hosts.items():
        if host.machine_id is not None and host.machine_id == machine_id:
            return host_id
    return None


def foreign_tiers(estate: Estate, host_id: str) -> frozenset[str]:
    """Tiers whose claims in ``host_id``'s inventory describe another host's volumes."""
    owned = {vol.tier for vol in estate.volumes.values() if vol.owner == host_id}
    return frozenset(vol.tier for vol in estate.volumes.values() if vol.owner != host_id) - owned


__all__ = ["foreign_tiers", "host_for_machine"]
