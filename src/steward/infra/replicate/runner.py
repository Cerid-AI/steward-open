# SPDX-License-Identifier: Apache-2.0

"""Top-level runner for ``steward replicate run``.

Reads a :class:`ReplicationPolicy`, walks each enabled
:class:`ReplicationSource`, invokes rclone, and writes an audit-log
entry per source. The audit pair (``replicate_start`` /
``replicate_end``) brackets the entire policy run so the chain
captures both successes and failures.

Per ADR-0009 (pull-don't-push), Steward's role is to plan + invoke +
record. The actual byte movement is rclone's; Steward never bypasses
the dry-run gate.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from steward.core.policy.schema import ReplicationPolicy, ReplicationSource
from steward.infra.db import repo_audit, repo_meta
from steward.infra.db.backup import BackupError, backup_inventory_db
from steward.infra.estate.guard import Check, load_guard
from steward.infra.replicate.rclone import (
    RcloneRunResult,
    run_rclone,
)

_STAGING_DIRNAME = ".replicate-staging"


@dataclass(frozen=True, slots=True)
class SourceReport:
    """Outcome of replicating one :class:`ReplicationSource`."""

    name: str
    source: str
    destination: str
    mode: str
    dry_run: bool
    skipped: bool  # ``enabled = False`` in the policy
    result: RcloneRunResult | None  # ``None`` when skipped


@dataclass
class ReplicationReport:
    """Aggregate report for one ``steward replicate run`` invocation."""

    policy_name: str
    sources: list[SourceReport] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    @property
    def runs(self) -> int:
        return sum(1 for s in self.sources if s.result is not None)

    @property
    def successes(self) -> int:
        return sum(1 for s in self.sources if s.result is not None and s.result.returncode == 0)

    @property
    def failures(self) -> int:
        return sum(1 for s in self.sources if s.result is not None and s.result.returncode != 0)

    @property
    def skipped(self) -> int:
        return sum(1 for s in self.sources if s.skipped)

    @property
    def bytes_transferred(self) -> int:
        total = 0
        for s in self.sources:
            if s.result is None:
                continue
            n = s.result.stats.get("bytes", 0)
            if isinstance(n, (int, float)):
                total += int(n)
        return total


def _summarise_for_audit(
    r: RcloneRunResult,
) -> dict[str, object]:
    """Compact payload of an :class:`RcloneRunResult` for audit_log.

    Avoids dumping the full ``stderr_tail`` — that text can be large
    and the audit chain is meant for compact attestation, not log
    archival.
    """
    return {
        "returncode": r.returncode,
        "timed_out": r.timed_out,
        "duration_seconds": round(r.duration_seconds, 3),
        "stats": r.stats,
        "command": list(r.command),
    }


def _snapshot_for_rclone(src: ReplicationSource, *, machine_id: str) -> Path:
    """Write a quick-checked online-backup copy of ``src.source`` for rclone."""
    live = Path(src.source)
    staging = Path(src.staging_dir).expanduser() if src.staging_dir else live.parent / _STAGING_DIRNAME
    staging.mkdir(parents=bool(src.staging_dir), exist_ok=True)
    staged = staging / live.name
    backup_inventory_db(source_path=live, target_path=staged, machine_id=machine_id, overwrite=True)
    check = sqlite3.connect(str(staged))
    try:
        verdict = check.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        check.close()
    if verdict != "ok":
        staged.unlink(missing_ok=True)
        raise BackupError(f"snapshot of {live} failed quick_check: {verdict}")
    return staged


def _not_run(reason: str) -> RcloneRunResult:
    return RcloneRunResult(
        returncode=1,
        duration_seconds=0.0,
        stdout="",
        stderr_tail=reason,
        stats={},
        command=(),
        timed_out=False,
    )


def run_replication(
    *,
    con: sqlite3.Connection,
    policy: ReplicationPolicy,
    machine_id: str,
    dry_run: bool,
    policy_name: str = "replication.yml",
) -> ReplicationReport:
    """Execute a :class:`ReplicationPolicy` end-to-end.

    The runner appends a ``replicate_start`` row, iterates sources (one
    rclone subprocess each), appends a ``replicate_source`` row per
    source with the compact result payload, then appends
    ``replicate_end`` with the aggregate counts. Each row is committed
    as it is written: a run can take hours, and an open write
    transaction would block every other writer, including the
    snapshot's own audit row, for that long.

    ``kind: sqlite-snapshot`` sources ship a quick-checked online-backup
    copy staged in ``.replicate-staging/`` beside the database; the copy
    is removed afterwards. A snapshot failure counts as a failed source.

    With an estate, every destination must sit inside one of this host's
    replica grants. In ``enforce`` mode a refused source is not run and
    counts as a failed source (``replicate_refused_namespace`` audit row,
    ``ownership_refused`` in its ``replicate_source`` row); in ``report``
    mode it runs after an ``ownership_would_refuse`` row. An unknown host
    or a foreign inventory.db refuses the whole run before any row.

    ``dry_run`` propagates to rclone via ``--dry-run`` — no bytes move
    on either side, and no snapshot is taken.
    """
    guard = load_guard(db_machine_id=repo_meta.get(con, "machine_id"))
    if guard is not None:
        guard.require_identity()
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-replicate",
        action="replicate_start",
        payload={
            "policy_name": policy_name,
            "dry_run": dry_run,
            "source_count": len(policy.sources),
            "started_at": started,
        },
    )
    con.commit()

    report = ReplicationReport(policy_name=policy_name, started_at=started)

    for src in policy.sources:
        if not src.enabled:
            report.sources.append(
                SourceReport(
                    name=src.name,
                    source=src.source,
                    destination=src.destination,
                    mode=src.mode,
                    dry_run=dry_run,
                    skipped=True,
                    result=None,
                )
            )
            continue

        snapshot_error: str | None = None
        ownership_refused: str | None = None
        staged: Path | None = None
        if guard is not None:
            refusals = guard.refusals(
                [Check("replicate-destination", os.path.expanduser(src.destination), "destination", src.name)]
            )
            if refusals:
                guard.audit(
                    con,
                    refusals,
                    refusal_action="replicate_refused_namespace",
                    machine_id=machine_id,
                    actor="steward-replicate",
                    context={"policy_name": policy_name, "source_name": src.name, "dry_run": dry_run},
                )
                con.commit()
                if guard.enforcing:
                    ownership_refused = refusals[0].decision.reason
        try:
            if ownership_refused is not None:
                result = _not_run(f"refused by volume ownership: {ownership_refused}")
            elif src.kind == "sqlite-snapshot" and not dry_run:
                try:
                    staged = _snapshot_for_rclone(src, machine_id=machine_id)
                except (BackupError, sqlite3.Error, OSError) as exc:
                    snapshot_error = str(exc)
                    result = _not_run(f"snapshot failed: {exc}")
                else:
                    result = run_rclone(
                        defaults=policy.defaults,
                        source=src.model_copy(update={"source": str(staged)}),
                        dry_run=dry_run,
                    )
            else:
                result = run_rclone(
                    defaults=policy.defaults,
                    source=src,
                    dry_run=dry_run,
                )
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
        sr = SourceReport(
            name=src.name,
            source=src.source,
            destination=src.destination,
            mode=src.mode,
            dry_run=dry_run,
            skipped=False,
            result=result,
        )
        report.sources.append(sr)

        repo_audit.append(
            con,
            machine_id=machine_id,
            actor="steward-replicate",
            action="replicate_source",
            payload={
                "policy_name": policy_name,
                "source_name": src.name,
                "source": src.source,
                "destination": src.destination,
                "mode": src.mode,
                "kind": src.kind,
                "dry_run": dry_run,
                **_summarise_for_audit(result),
                **({"snapshot_error": snapshot_error} if snapshot_error else {}),
                **({"ownership_refused": ownership_refused} if ownership_refused else {}),
            },
        )
        con.commit()

    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report.finished_at = finished
    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-replicate",
        action="replicate_end",
        payload={
            "policy_name": policy_name,
            "dry_run": dry_run,
            "runs": report.runs,
            "successes": report.successes,
            "failures": report.failures,
            "skipped": report.skipped,
            "bytes_transferred": report.bytes_transferred,
            "finished_at": finished,
        },
    )
    con.commit()
    return report


__all__ = ["ReplicationReport", "SourceReport", "run_replication"]
