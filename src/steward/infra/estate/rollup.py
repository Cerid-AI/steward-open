# SPDX-License-Identifier: Apache-2.0

"""Estate host labels and owner-only claim filters for the cross-machine read surfaces.

Without an estate file, or on a machine the estate does not list, both are
empty and every caller's SQL is unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from steward.core.estate.rollup import foreign_tiers, host_for_machine
from steward.infra.estate.active import active_estate

if TYPE_CHECKING:
    from steward.infra.sync.attach import AttachContext


def host_labels(local_machine_id: str | None) -> dict[str, str]:
    """``machine_id -> host id`` for every machine the estate can name.

    Pinned machine_ids name their host; the local inventory is this host
    even when the estate leaves its machine_id unpinned.
    """
    ctx = active_estate()
    if ctx.legacy:
        return {}
    hosts = ctx.estate.hosts
    labels = {host.machine_id: host_id for host_id, host in hosts.items() if host.machine_id is not None}
    if local_machine_id:
        labels[local_machine_id] = ctx.host_id
    return labels


def claim_exclusions(ctx: AttachContext) -> dict[str, frozenset[str]]:
    """Schema prefix (``""`` for local, else the ATTACH alias) -> tiers to leave out of a rollup."""
    active = active_estate()
    if active.legacy:
        return {}
    estate = active.estate
    out: dict[str, frozenset[str]] = {"": foreign_tiers(estate, active.host_id)}
    for schema in ctx.attached:
        host_id = host_for_machine(estate, schema.machine_id)
        if host_id is not None:
            out[schema.alias] = foreign_tiers(estate, host_id)
    return {alias: tiers for alias, tiers in out.items() if tiers}


def owned_claims_where(exclusions: Mapping[str, frozenset[str]] | None, schema: str) -> str:
    """`` WHERE tier NOT IN (…)`` for ``schema``'s claims, or ``""`` when nothing is excluded."""
    tiers = (exclusions or {}).get(schema)
    if not tiers:
        return ""
    # Estate tier names are validated to [A-Za-z][A-Za-z0-9_-]*, so they are safe as SQL literals.
    return " WHERE tier NOT IN (" + ", ".join(f"'{t}'" for t in sorted(tiers)) + ")"


__all__ = ["claim_exclusions", "host_labels", "owned_claims_where"]
