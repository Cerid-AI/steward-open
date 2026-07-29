# SPDX-License-Identifier: Apache-2.0

"""Stash facade — list / finalize / restore cooling-off entries.

Backed by audit_log queries: every ``stash_committed`` row is an
in-flight stash entry until a matching ``stash_finalized`` or
``stash_restored`` row appears with the same ``manifest_run_id`` and
source path. The audit chain stays the source of truth — there's no
separate "stash entries" table.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from steward.core.errors import ManifestError
from steward.infra.db import repo_audit
from steward.infra.db.connect import connect
from steward.infra.db.settings import inventory_db_path

logger = logging.getLogger("steward.infra.db.stash_cmd")


@dataclass(frozen=True)
class StashEntry:
    manifest_run_id: str
    source_path: str
    destination_path: str
    permanode_id: str | None
    """FK-safe id from ``audit_log.permanode_id``. ``None`` when the
    manifest's permanode wasn't in the DB at apply time (stash.py nulls
    the FK and stashes the original id under ``manifest_permanode_id``
    in the payload)."""
    manifest_permanode_id: str | None
    """Original id from the manifest payload when the FK column was
    nulled. ``None`` when the FK column held the value (the common
    case)."""
    committed_at: datetime
    rationale: str

    @property
    def age_days(self) -> float:
        return (datetime.now(timezone.utc) - self.committed_at).total_seconds() / 86400

    @property
    def effective_permanode_id(self) -> str | None:
        """The id to use for permanode lookups — prefers the FK-safe
        value, falls back to the original manifest id. Used by
        :func:`verify_stash` to chase a canonical-elsewhere claim."""
        return self.permanode_id or self.manifest_permanode_id


@dataclass
class StashGroup:
    manifest_run_id: str
    entries: list[StashEntry] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def oldest_age_days(self) -> float:
        return max((e.age_days for e in self.entries), default=0.0)


def list_stashes(*, db_path: Path | None = None) -> list[StashGroup]:
    """Walk audit_log + return all in-flight stash entries grouped by
    ``manifest_run_id``.

    "In-flight" == ``stash_committed`` exists but no later ``stash_finalized``
    or ``stash_restored`` row covers the same (manifest_run_id, source_path).
    """
    target = (db_path or inventory_db_path()).expanduser()
    if not target.exists():
        return []

    con = connect(target, read_only=True, load_vec=False)
    try:
        rows = list(
            con.execute(
                """
                SELECT timestamp, action, permanode_id, manifest_run_id, payload_json
                FROM audit_log
                WHERE action IN ('stash_committed', 'stash_finalized', 'stash_restored')
                ORDER BY id ASC
                """
            )
        )
    finally:
        con.close()

    # Track (run_id, source) → committed payload. Remove on finalize/restore.
    active: dict[tuple[str, str], StashEntry] = {}
    for ts, action, permanode_id, run_id, payload_json in rows:
        if run_id is None:
            continue
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            continue
        src = payload.get("source")
        if not src:
            continue
        key = (run_id, src)
        if action == "stash_committed":
            # When the manifest's permanode_id wasn't resolvable, stash.py
            # NULLs the audit FK and carries the original id in
            # ``payload.manifest_permanode_id``. Track both so verify can
            # use the original id without breaking finalize's FK-safe
            # audit appends.
            active[key] = StashEntry(
                manifest_run_id=run_id,
                source_path=src,
                destination_path=payload.get("destination", ""),
                permanode_id=permanode_id,
                manifest_permanode_id=payload.get("manifest_permanode_id"),
                committed_at=_parse_iso(ts),
                rationale=payload.get("rationale", ""),
            )
        else:
            active.pop(key, None)

    groups: dict[str, StashGroup] = defaultdict(lambda: StashGroup(manifest_run_id=""))
    for entry in active.values():
        g = groups.setdefault(entry.manifest_run_id, StashGroup(manifest_run_id=entry.manifest_run_id))
        g.entries.append(entry)
    return sorted(groups.values(), key=lambda g: g.manifest_run_id)


def finalize_stash(
    *,
    manifest_run_id: str,
    machine_id: str,
    cooling_off_days: int = 7,
    force: bool = False,
    db_path: Path | None = None,
) -> dict[str, int]:
    """Permanently delete the destination files for every in-flight entry
    of ``manifest_run_id``. Append ``stash_finalized`` audit rows.

    Refuses entries younger than ``cooling_off_days`` unless ``force=True``.

    Returns counts: ``{"finalized": N, "skipped_young": N, "errored": N}``.
    """
    target = (db_path or inventory_db_path()).expanduser()
    if not target.exists():
        raise ManifestError(f"inventory.db not found at {target}")
    groups = list_stashes(db_path=target)
    match = next((g for g in groups if g.manifest_run_id == manifest_run_id), None)
    if match is None:
        return {"finalized": 0, "skipped_young": 0, "errored": 0}

    counts = {"finalized": 0, "skipped_young": 0, "errored": 0}
    con = connect(target)
    try:
        for entry in match.entries:
            if not force and entry.age_days < cooling_off_days:
                counts["skipped_young"] += 1
                continue
            dst = Path(entry.destination_path)
            if dst.exists():
                try:
                    os.unlink(dst)
                except OSError as exc:
                    counts["errored"] += 1
                    repo_audit.append(
                        con,
                        machine_id=machine_id,
                        actor="steward-stash-finalize",
                        action="stash_finalize_failed",
                        payload={
                            "source": entry.source_path,
                            "destination": str(dst),
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                        permanode_id=entry.permanode_id,
                        manifest_run_id=manifest_run_id,
                    )
                    continue
            repo_audit.append(
                con,
                machine_id=machine_id,
                actor="steward-stash-finalize",
                action="stash_finalized",
                payload={
                    "source": entry.source_path,
                    "destination": str(dst),
                    "age_days": round(entry.age_days, 3),
                    "force": force,
                },
                permanode_id=entry.permanode_id,
                manifest_run_id=manifest_run_id,
            )
            counts["finalized"] += 1
        con.commit()
    finally:
        con.close()
    return counts


def restore_stash(
    *,
    manifest_run_id: str,
    machine_id: str,
    db_path: Path | None = None,
) -> dict[str, int]:
    """Move every in-flight entry of ``manifest_run_id`` back to its
    original source path. Append ``stash_restored`` audit rows.

    Refuses individual entries whose original source path is already
    occupied (the operator must clear the way first).
    """
    target = (db_path or inventory_db_path()).expanduser()
    if not target.exists():
        raise ManifestError(f"inventory.db not found at {target}")
    groups = list_stashes(db_path=target)
    match = next((g for g in groups if g.manifest_run_id == manifest_run_id), None)
    if match is None:
        return {"restored": 0, "skipped_occupied": 0, "errored": 0}

    counts = {"restored": 0, "skipped_occupied": 0, "errored": 0}
    con = connect(target)
    try:
        for entry in match.entries:
            dst = Path(entry.destination_path)
            src = Path(entry.source_path)
            if not dst.exists():
                counts["errored"] += 1
                continue
            if src.exists():
                counts["skipped_occupied"] += 1
                continue
            try:
                src.parent.mkdir(parents=True, exist_ok=True)
                os.rename(dst, src)
            except OSError as exc:
                counts["errored"] += 1
                repo_audit.append(
                    con,
                    machine_id=machine_id,
                    actor="steward-stash-restore",
                    action="stash_restore_failed",
                    payload={
                        "source": str(src),
                        "destination": str(dst),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    permanode_id=entry.permanode_id,
                    manifest_run_id=manifest_run_id,
                )
                continue
            repo_audit.append(
                con,
                machine_id=machine_id,
                actor="steward-stash-restore",
                action="stash_restored",
                payload={"source": str(src), "destination": str(dst)},
                permanode_id=entry.permanode_id,
                manifest_run_id=manifest_run_id,
            )
            counts["restored"] += 1
        con.commit()
    finally:
        con.close()
    return counts


def _parse_iso(s: object) -> datetime:
    try:
        return datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


VerifyStatus = Literal[
    "ok",
    "dst-missing",
    "src-still-present",
    "no-canonical-elsewhere",
    "no-permanode",
    "error",
]


@dataclass(frozen=True)
class StashVerifyResult:
    """One row of ``stash verify`` output. Each in-flight entry produces
    exactly one result."""

    source_path: str
    destination_path: str
    permanode_id: str | None
    status: VerifyStatus
    canonical_path: str | None  # set when status == "ok"
    error: str | None  # set when status == "error"


def _find_canonical_outside(
    con: object,
    *,
    permanode_id: str,
    excluded_prefixes: list[str],
) -> str | None:
    """Return the file_path of a current claim with this permanode_id whose
    path is NOT under any of ``excluded_prefixes``. ``None`` if every
    current claim falls inside an excluded prefix.
    """
    import sqlite3

    assert isinstance(con, sqlite3.Connection)
    cur = con.execute(
        """
        SELECT file_path FROM claims
        WHERE permanode_id = ? AND is_current = 1
        """,
        (permanode_id,),
    )
    for (path,) in cur:
        spath = str(path)
        inside = False
        for ep in excluded_prefixes:
            ep_norm = ep.rstrip("/")
            if spath == ep_norm or spath.startswith(ep_norm + "/"):
                inside = True
                break
        if not inside:
            return spath
    return None


def verify_stash(
    *,
    manifest_run_id: str,
    db_path: Path | None = None,
    also_exclude: list[str] | None = None,
) -> tuple[list[StashVerifyResult], dict[str, int]]:
    """Verify every in-flight entry of ``manifest_run_id`` is safe to finalize.

    Three checks per entry:

    1. **dst-missing** — destination file vanished from the cooling-off
       stash. The audit log thinks it's there; disk says otherwise.
    2. **src-still-present** — the original source path is occupied. The
       ``apply --execute`` rename either didn't complete or someone
       re-created the file. Either way, finalize would orphan data.
    3. **no-canonical-elsewhere** — every other current claim for this
       permanode is also under the stash (or an additionally-excluded
       prefix). Finalize would destroy the last copy.

    ``also_exclude`` lets the caller treat multiple related stash groups
    as a unit — a copy under any of them does NOT count as canonical.

    Returns ``(results, summary_counts)`` where ``summary_counts`` is
    ``{"total": N, "ok": N, "dst-missing": N, "src-still-present": N,
       "no-canonical-elsewhere": N, "no-permanode": N, "error": N}``.
    """
    target = (db_path or inventory_db_path()).expanduser()
    if not target.exists():
        raise ManifestError(f"inventory.db not found at {target}")
    groups = list_stashes(db_path=target)
    match = next((g for g in groups if g.manifest_run_id == manifest_run_id), None)
    counts: dict[str, int] = {
        "total": 0,
        "ok": 0,
        "dst-missing": 0,
        "src-still-present": 0,
        "no-canonical-elsewhere": 0,
        "no-permanode": 0,
        "error": 0,
    }
    if match is None:
        return [], counts

    # Build the prefix-exclusion list: every destination_path's parent
    # plus any operator-supplied also_exclude prefixes.
    parents = sorted({str(Path(e.destination_path).parent) for e in match.entries})
    excluded_prefixes = list(parents) + list(also_exclude or [])

    results: list[StashVerifyResult] = []
    con = connect(target, read_only=True, load_vec=False)
    try:
        for entry in match.entries:
            counts["total"] += 1
            dst = Path(entry.destination_path)
            src = Path(entry.source_path)

            if not dst.exists():
                results.append(
                    StashVerifyResult(
                        source_path=entry.source_path,
                        destination_path=entry.destination_path,
                        permanode_id=entry.permanode_id,
                        status="dst-missing",
                        canonical_path=None,
                        error=None,
                    )
                )
                counts["dst-missing"] += 1
                continue

            if src.exists():
                results.append(
                    StashVerifyResult(
                        source_path=entry.source_path,
                        destination_path=entry.destination_path,
                        permanode_id=entry.permanode_id,
                        status="src-still-present",
                        canonical_path=None,
                        error=None,
                    )
                )
                counts["src-still-present"] += 1
                continue

            pid = entry.effective_permanode_id
            if pid is None:
                results.append(
                    StashVerifyResult(
                        source_path=entry.source_path,
                        destination_path=entry.destination_path,
                        permanode_id=None,
                        status="no-permanode",
                        canonical_path=None,
                        error=None,
                    )
                )
                counts["no-permanode"] += 1
                continue

            canonical = _find_canonical_outside(
                con,
                permanode_id=pid,
                excluded_prefixes=excluded_prefixes,
            )
            if canonical is None:
                results.append(
                    StashVerifyResult(
                        source_path=entry.source_path,
                        destination_path=entry.destination_path,
                        permanode_id=pid,
                        status="no-canonical-elsewhere",
                        canonical_path=None,
                        error=None,
                    )
                )
                counts["no-canonical-elsewhere"] += 1
                continue

            results.append(
                StashVerifyResult(
                    source_path=entry.source_path,
                    destination_path=entry.destination_path,
                    permanode_id=pid,
                    status="ok",
                    canonical_path=canonical,
                    error=None,
                )
            )
            counts["ok"] += 1
    finally:
        con.close()
    return results, counts
