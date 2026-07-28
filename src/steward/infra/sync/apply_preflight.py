# SPDX-License-Identifier: Apache-2.0

"""Pre-flight check enforcing pull-don't-push on cross-machine apply (ADR-0013).

Structural guarantee: ``steward apply`` must never act on a row whose
``permanode_id`` came from an attached (imported) inventory rather
than the local one. ADR-0009 declares the invariant; this module
enforces it.

The check is **opportunistic** — it fires only when at least one
``attached_inventories`` row exists. On a single-machine install
the check is a no-op (no attached schemas means no foreign-claim
risk), keeping the v0.1 / v0.2 apply path unchanged.

When attached inventories are present:

1. Open the local DB.
2. For each ``attached_inventories`` row, ``ATTACH DATABASE
   'file:...?mode=ro' AS m_<short_id>``.
3. For each manifest row, classify the ``permanode_id``:

   * ``LOCAL_HIT`` — at least one row in the local ``claims`` table
     with this permanode_id and ``machine_id`` matching the local
     machine. Safe to apply.
   * ``LOCAL_MISS_ATTACHED_HIT`` — the permanode_id is unknown
     locally but at least one attached inventory has a claim for it.
     **Refused**: this row came from a foreign inventory.
   * ``LOCAL_MISS_ATTACHED_MISS`` — the permanode_id appears in
     neither local nor any attached schema. Allowed (the row is
     either operator-injected or pending a scan; apply's own
     downstream verification will catch broken paths).

4. Detach all attached schemas.
5. Return :class:`ApplyPreflightReport` listing every rejected row.

The caller (``apply_manifest``) refuses the apply if rejections
are non-empty AND writes one ``apply_rejected_imported_claim``
audit row per refusal so the operator's chain captures the event.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from steward.infra.db.connect import connect
from steward.infra.db.settings import inventory_db_path
from steward.infra.observability import log_swallowed_error

if TYPE_CHECKING:
    from pathlib import Path

    from steward.core.model.manifest import Manifest, ManifestRow

logger = logging.getLogger("steward.infra.sync.apply_preflight")


@dataclass(frozen=True, slots=True)
class RejectedRow:
    """One manifest row refused by the pre-flight."""

    row_index: int
    permanode_id: str
    source_path: str
    reason: str
    found_in_machine_id: str  # which attached inventory owns the row


@dataclass(frozen=True, slots=True)
class ApplyPreflightReport:
    """Roll-up of pre-flight rejections.

    A report with ``rejections == []`` means the apply is cleared to
    proceed; the caller need not consult ``attached_inspected`` etc.
    Empty reports are produced cheaply when the local DB has no
    attached inventories — most installs see this path forever.
    """

    rejections: list[RejectedRow]
    attached_inspected: int
    """Number of attached inventories examined. Zero on installs with
    no imports — the common single-machine case."""

    @property
    def ok(self) -> bool:
        return not self.rejections


def _attach_alias(machine_id: str) -> str:
    """Make a stable SQL identifier from a machine_id UUID.

    SQLite ATTACH AS requires an identifier (letters, digits,
    underscores). Strip the dashes; prepend ``m_`` so we never
    start with a digit.
    """
    return "m_" + machine_id.replace("-", "")[:24]


def preflight_apply(
    *,
    manifest: "Manifest",
    machine_id: str,
    db_path: "Path | None" = None,
) -> ApplyPreflightReport:
    """Validate ``manifest`` doesn't reference foreign-claim rows.

    Parameters
    ----------
    manifest:
        Parsed :class:`steward.core.model.manifest.Manifest` to check.
    machine_id:
        Local machine's UUID (the apply runs as this machine).
    db_path:
        Override for the local inventory.db; defaults to
        :func:`inventory_db_path`.

    Returns an :class:`ApplyPreflightReport`. Caller decides what
    to do with non-empty ``rejections``.

    Never raises on a structural problem — refusals come back as
    rows in the report. Raises on infrastructure failures
    (database open error, etc.) because those are operator-side bugs
    that need surfacing.
    """
    target_path = (db_path or inventory_db_path()).expanduser()

    # ── Fast path: no attached inventories means no cross-machine risk. ──
    con_probe = connect(target_path, read_only=True, load_vec=False)
    try:
        rows = con_probe.execute(
            "SELECT machine_id, file_path FROM attached_inventories"
        ).fetchall()
    finally:
        con_probe.close()

    if not rows:
        return ApplyPreflightReport(rejections=[], attached_inspected=0)

    # ── Slow path: attach each imported .db read-only, classify each row. ──
    con = connect(target_path, read_only=False, load_vec=False)
    rejections: list[RejectedRow] = []
    attached_aliases: list[str] = []
    try:
        # Attach each available payload. Skip rows whose payload is
        # missing — the operator should have run `imports detach` first,
        # but a missing file can't host a claim so it's not a risk for
        # the pre-flight.
        attached: list[tuple[str, str]] = []  # (alias, machine_id)
        for row in rows:
            ext_machine_id = str(row[0])
            payload_path = str(row[1])
            alias = _attach_alias(ext_machine_id)
            try:
                con.execute(
                    f"ATTACH DATABASE 'file:{payload_path}?mode=ro' AS {alias}"
                )
            except sqlite3.OperationalError as exc:  # noqa: BLE001 — missing file isn't fatal
                log_swallowed_error(
                    "infra.sync.apply_preflight.attach",
                    exc,
                    context={"path": payload_path, "alias": alias},
                )
                continue
            attached_aliases.append(alias)
            attached.append((alias, ext_machine_id))

        # Classify every manifest row.
        for idx, m_row in enumerate(manifest.rows):
            _classify_row(
                con=con,
                row=m_row,
                row_index=idx,
                machine_id=machine_id,
                attached=attached,
                rejections=rejections,
            )
    finally:
        # Detach in reverse order. Detach errors are non-fatal — the
        # connection closes immediately after anyway.
        for alias in reversed(attached_aliases):
            try:
                con.execute(f"DETACH DATABASE {alias}")
            except sqlite3.OperationalError as exc:  # noqa: BLE001
                log_swallowed_error(
                    "infra.sync.apply_preflight.detach",
                    exc,
                    context={"alias": alias},
                )
        con.close()

    return ApplyPreflightReport(
        rejections=rejections,
        attached_inspected=len(attached_aliases),
    )


def _classify_row(
    *,
    con: sqlite3.Connection,
    row: "ManifestRow",
    row_index: int,
    machine_id: str,
    attached: list[tuple[str, str]],
    rejections: list[RejectedRow],
) -> None:
    """Append to ``rejections`` iff ``row`` is foreign-only."""
    # Local hit? Look for any current claim for this permanode on the
    # local machine. Any hit clears the row.
    local_hit = con.execute(
        "SELECT 1 FROM claims "
        "WHERE permanode_id = ? AND machine_id = ? AND is_current = 1 "
        "LIMIT 1",
        (row.permanode_id, machine_id),
    ).fetchone()
    if local_hit is not None:
        return

    # No local hit — check whether any attached inventory has a claim
    # for this permanode. If yes, REFUSE. If no, allow (this is a
    # row about a permanode this whole system doesn't know about;
    # apply's downstream path-existence checks will catch it if the
    # file path is bogus).
    for alias, ext_machine_id in attached:
        # The alias is constructed by _attach_alias from a stored
        # machine_id (already validated as UUID-shaped at import
        # time). SQLite doesn't bind schema names — interpolation is
        # the only path; the value is not user-supplied here.
        attached_hit = con.execute(
            f"SELECT 1 FROM {alias}.claims "  # nosec B608 — alias from controlled allowlist
            "WHERE permanode_id = ? LIMIT 1",
            (row.permanode_id,),
        ).fetchone()
        if attached_hit is not None:
            rejections.append(
                RejectedRow(
                    row_index=row_index,
                    permanode_id=row.permanode_id,
                    source_path=row.source_path,
                    reason=(
                        "permanode_id only exists in attached inventory "
                        f"{ext_machine_id[:8]}…; ADR-0013 forbids applying "
                        "non-local claims."
                    ),
                    found_in_machine_id=ext_machine_id,
                )
            )
            return  # one rejection per row is enough


__all__ = [
    "ApplyPreflightReport",
    "RejectedRow",
    "preflight_apply",
]
