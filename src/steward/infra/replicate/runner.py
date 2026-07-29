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

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from steward.core.policy.schema import ReplicationPolicy
from steward.infra.db import repo_audit
from steward.infra.replicate.rclone import (
    RcloneRunResult,
    run_rclone,
)


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


def run_replication(
    *,
    con: sqlite3.Connection,
    policy: ReplicationPolicy,
    machine_id: str,
    dry_run: bool,
    policy_name: str = "replication.yml",
) -> ReplicationReport:
    """Execute a :class:`ReplicationPolicy` end-to-end.

    Caller owns the transaction. The runner appends a
    ``replicate_start`` row, iterates sources (one rclone subprocess
    each), appends a ``replicate_source`` row per source with the
    compact result payload, then appends ``replicate_end`` with the
    aggregate counts.

    ``dry_run`` propagates to rclone via ``--dry-run`` — no bytes move
    on either side.
    """
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

        result = run_rclone(
            defaults=policy.defaults,
            source=src,
            dry_run=dry_run,
        )
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
                "dry_run": dry_run,
                **_summarise_for_audit(result),
            },
        )

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
    return report


__all__ = ["ReplicationReport", "SourceReport", "run_replication"]
