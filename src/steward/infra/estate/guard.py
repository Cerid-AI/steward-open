# SPDX-License-Identifier: Apache-2.0

"""Volume-ownership enforcement: may this host act on these paths?

Wraps :mod:`steward.core.estate.ownership` with the loaded estate, this
machine's host identity and the inventory.db binding. :func:`load_guard`
returns ``None`` when there is no estate file; callers then skip every check
and write no audit rows, so a single-host install behaves exactly as before.

With an estate:

* an unknown host, or an inventory.db bound to another machine, refuses every
  gated action whatever the enforcement mode (:class:`UnknownHostError`,
  :class:`BindingMismatchError`), before anything is written;
* ``report`` — refusals are audited as ``ownership_would_refuse`` and the
  action proceeds;
* ``enforce`` — refusals are audited under the caller's refusal action
  (``apply_rejected_foreign_volume``, ``scan_refused_foreign_volume``, …)
  and the action is refused.

Audit rows are aggregated per (check, role, volume): one row carries the
count and a sample of paths, so a manifest of 100k foreign rows does not
add 100k rows to the chain on every dry-run.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steward.core.estate import Action, Decision, Estate, OwnershipPolicy
from steward.core.estate.schema import Enforcement
from steward.core.model.manifest import ManifestRow
from steward.infra.db import repo_audit
from steward.infra.db.admin import read_machine_id
from steward.infra.db.connect import connect
from steward.infra.estate.active import active_estate
from steward.infra.estate.host import (
    Binding,
    HostIdentity,
    check_binding,
    require_binding,
    require_known_host,
)
from steward.infra.estate.loader import current

WOULD_REFUSE = "ownership_would_refuse"
AUDIT_SAMPLES = 20


@dataclass(frozen=True, slots=True)
class Check:
    """One ownership question: may this host ``action`` on ``path``?"""

    action: Action
    path: str
    role: str
    """What ``path`` is to the caller: ``source``, ``destination``, ``root``, …"""
    ref: str | int | None = None
    """Caller's handle for the item (manifest row index, policy source name)."""


@dataclass(frozen=True, slots=True)
class Refusal:
    check: Check
    decision: Decision

    def describe(self) -> str:
        where = f"row {self.check.ref}: " if isinstance(self.check.ref, int) else ""
        return f"{where}{self.check.role} {self.check.path} — {self.decision.reason}"


def row_checks(index: int, row: ManifestRow) -> list[Check]:
    """The ownership checks applying ``row`` needs; empty for actions apply does not run."""
    dst = row.destination_path
    if row.action == "stash":
        return [Check("stash", row.source_path, "source", index)] + (
            [Check("stash", dst, "destination", index)] if dst else []
        )
    if row.action == "promote":
        return [Check("promote-source", row.source_path, "source", index)] + (
            [Check("promote-destination", dst, "destination", index)] if dst else []
        )
    if row.action == "retire_direct":
        return [Check("retire", row.source_path, "source", index)]
    if row.action == "nas_manifest":
        return [Check("nas_manifest", row.source_path, "source", index)]
    return []


def manifest_checks(rows: Iterable[ManifestRow]) -> list[Check]:
    return [check for i, row in enumerate(rows) for check in row_checks(i, row)]


class OwnershipGuard:
    """Ownership checks for this host against the loaded estate."""

    def __init__(self, estate: Estate, identity: HostIdentity, binding: Binding | None) -> None:
        self.estate = estate
        self.identity = identity
        self.binding = binding
        self._policy = OwnershipPolicy(estate, identity.host_id) if identity.host_id is not None else None

    @property
    def mode(self) -> Enforcement:
        return self.estate.enforcement

    @property
    def enforcing(self) -> bool:
        return self.mode == "enforce"

    def require_identity(self) -> str:
        """This host's id; raises when the host is unknown or the DB is another machine's."""
        host = require_known_host(self.identity)
        if self.binding is not None:
            require_binding(self.binding)
        return host

    def decide(self, action: Action, path: str) -> Decision:
        self.require_identity()
        assert self._policy is not None
        return self._policy.decide(action, path)

    def refusals(self, checks: Iterable[Check]) -> list[Refusal]:
        """Every check the estate does not allow (in either mode)."""
        out: list[Refusal] = []
        for check in checks:
            decision = self.decide(check.action, check.path)
            if not decision.allowed:
                out.append(Refusal(check, decision))
        return out

    def audit(
        self,
        con: sqlite3.Connection,
        refusals: list[Refusal],
        *,
        refusal_action: str,
        machine_id: str,
        actor: str,
        manifest_run_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Append the refusal rows inside the caller's transaction.

        ``report`` mode writes ``ownership_would_refuse``; ``enforce`` writes
        ``refusal_action``. Either way the payload names the refusal so
        ``estate check`` can count what enforcing would block.
        """
        groups: dict[tuple[str, str, str | None], list[Refusal]] = {}
        for refusal in refusals:
            key = (refusal.decision.action, refusal.check.role, refusal.decision.volume_id)
            groups.setdefault(key, []).append(refusal)
        action = refusal_action if self.enforcing else WOULD_REFUSE
        for (check_action, role, volume_id), items in groups.items():
            first = items[0].decision
            repo_audit.append(
                con,
                machine_id=machine_id,
                actor=actor,
                action=action,
                payload={
                    "refusal": refusal_action,
                    "mode": self.mode,
                    "host": self.identity.host_id,
                    "check": check_action,
                    "role": role,
                    "volume_id": volume_id,
                    "access": first.access,
                    "reason": first.reason,
                    "count": len(items),
                    "samples": [{"ref": r.check.ref, "path": r.check.path} for r in items[:AUDIT_SAMPLES]],
                    **(context or {}),
                },
                manifest_run_id=manifest_run_id,
            )

    def record(
        self,
        db_path: Path,
        refusals: list[Refusal],
        *,
        refusal_action: str,
        machine_id: str,
        actor: str,
        manifest_run_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """:meth:`audit` in a transaction of its own, committed before the caller acts."""
        con = connect(db_path)
        try:
            self.audit(
                con,
                refusals,
                refusal_action=refusal_action,
                machine_id=machine_id,
                actor=actor,
                manifest_run_id=manifest_run_id,
                context=context,
            )
            con.commit()
        finally:
            con.close()


def load_guard(*, db_path: Path | None = None, db_machine_id: str | None = None) -> OwnershipGuard | None:
    """The guard for this machine, or ``None`` without an estate file (legacy: no checks).

    The binding is checked against ``db_machine_id`` or, failing that, the
    machine_id stored in ``db_path`` (read only when an estate exists).
    """
    ctx = active_estate()
    estate = current()
    if estate is None or ctx.identity is None:
        return None
    identity = ctx.identity
    binding: Binding | None = None
    if identity.host_id is not None:
        if db_machine_id is None and db_path is not None:
            db_machine_id = read_machine_id(db_path)
        binding = check_binding(estate.hosts[identity.host_id], db_machine_id)
    return OwnershipGuard(estate, identity, binding)


@dataclass(frozen=True, slots=True)
class WouldRefuse:
    """``ownership_would_refuse`` rows recorded so far for one refusal and volume."""

    refusal: str
    volume_id: str | None
    check: str
    count: int
    events: int


def would_refuse_counts(db_path: Path) -> list[WouldRefuse]:
    """What ``enforce`` would have refused, summed from the report-mode audit rows."""
    if not db_path.exists():
        return []
    con = connect(db_path, read_only=True, load_vec=False)
    try:
        rows = con.execute("SELECT payload_json FROM audit_log WHERE action = ?", (WOULD_REFUSE,)).fetchall()
    finally:
        con.close()
    totals: dict[tuple[str, str | None, str], list[int]] = {}
    for (payload_json,) in rows:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            continue
        key = (str(payload.get("refusal", "?")), payload.get("volume_id"), str(payload.get("check", "?")))
        acc = totals.setdefault(key, [0, 0])
        acc[0] += int(payload.get("count", 1))
        acc[1] += 1
    return [
        WouldRefuse(refusal, volume_id, check, count, events)
        for (refusal, volume_id, check), (count, events) in sorted(
            totals.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2])
        )
    ]


__all__ = [
    "AUDIT_SAMPLES",
    "WOULD_REFUSE",
    "Check",
    "OwnershipGuard",
    "Refusal",
    "WouldRefuse",
    "load_guard",
    "manifest_checks",
    "row_checks",
    "would_refuse_counts",
]
