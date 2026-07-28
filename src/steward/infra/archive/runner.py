# SPDX-License-Identifier: Apache-2.0

"""Policy-driven runner for ``steward archive``.

Three operations:

* :func:`run_archive_snapshot` — iterate enabled sources, invoke
  ``restic backup``, write audit entries.
* :func:`run_archive_list` — call ``restic snapshots`` per unique
  repository in the policy. Returns the union as one report.
* :func:`run_archive_init` — call ``restic init`` per unique
  repository. Used once at setup time. Per ADR-0002, the CLI requires
  explicit ``--execute`` to call this.
"""
from __future__ import annotations

import sqlite3
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from steward.core.policy.schema import ArchivePolicy
from steward.infra.archive.restic import (
    ResticRunResult,
    run_restic_backup,
    run_restic_init,
    run_restic_snapshots,
)
from steward.infra.db import repo_audit


@dataclass(frozen=True, slots=True)
class SnapshotReport:
    """Outcome of snapshotting one :class:`ArchiveSource`."""

    name: str
    source: str
    repository: str
    dry_run: bool
    skipped: bool  # ``enabled = False`` in policy
    result: ResticRunResult | None


@dataclass
class ArchiveSnapshotReport:
    """Aggregate report for one ``steward archive snapshot`` invocation."""

    policy_name: str
    sources: list[SnapshotReport] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    @property
    def runs(self) -> int:
        return sum(1 for s in self.sources if s.result is not None)

    @property
    def successes(self) -> int:
        return sum(
            1
            for s in self.sources
            if s.result is not None and s.result.returncode == 0
        )

    @property
    def failures(self) -> int:
        return sum(
            1
            for s in self.sources
            if s.result is not None and s.result.returncode != 0
        )

    @property
    def skipped(self) -> int:
        return sum(1 for s in self.sources if s.skipped)

    @property
    def total_bytes_added(self) -> int:
        total = 0
        for s in self.sources:
            if s.result is None:
                continue
            n = s.result.summary.get("data_added", 0)
            if isinstance(n, (int, float)):
                total += int(n)
        return total


@dataclass
class ArchiveListReport:
    """One row per restic snapshot from one or more repositories."""

    policy_name: str
    repositories: list[str] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)


def _summarise_for_audit(r: ResticRunResult) -> dict[str, object]:
    """Compact payload of a :class:`ResticRunResult` for audit_log."""
    return {
        "op": r.op,
        "returncode": r.returncode,
        "timed_out": r.timed_out,
        "duration_seconds": round(r.duration_seconds, 3),
        "summary": r.summary,
        "command": list(r.command),
    }


# ─────────────────────── snapshot ──────────────────────────


def run_archive_snapshot(
    *,
    con: sqlite3.Connection,
    policy: ArchivePolicy,
    machine_id: str,
    dry_run: bool,
    policy_name: str = "archive.yml",
) -> ArchiveSnapshotReport:
    """Run ``restic backup`` once per enabled source. Caller owns the txn."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-archive",
        action="archive_start",
        payload={
            "policy_name": policy_name,
            "operation": "snapshot",
            "dry_run": dry_run,
            "source_count": len(policy.sources),
            "started_at": started,
        },
    )

    report = ArchiveSnapshotReport(policy_name=policy_name, started_at=started)
    for src in policy.sources:
        if not src.enabled:
            report.sources.append(
                SnapshotReport(
                    name=src.name,
                    source=src.source,
                    repository=src.repository,
                    dry_run=dry_run,
                    skipped=True,
                    result=None,
                )
            )
            continue

        result = run_restic_backup(
            defaults=policy.defaults,
            source=src,
            dry_run=dry_run,
        )
        report.sources.append(
            SnapshotReport(
                name=src.name,
                source=src.source,
                repository=src.repository,
                dry_run=dry_run,
                skipped=False,
                result=result,
            )
        )
        repo_audit.append(
            con,
            machine_id=machine_id,
            actor="steward-archive",
            action="archive_source",
            payload={
                "policy_name": policy_name,
                "source_name": src.name,
                "source": src.source,
                "repository": src.repository,
                "dry_run": dry_run,
                **_summarise_for_audit(result),
            },
        )

    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report.finished_at = finished
    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-archive",
        action="archive_end",
        payload={
            "policy_name": policy_name,
            "operation": "snapshot",
            "dry_run": dry_run,
            "runs": report.runs,
            "successes": report.successes,
            "failures": report.failures,
            "skipped": report.skipped,
            "total_bytes_added": report.total_bytes_added,
            "finished_at": finished,
        },
    )
    return report


# ─────────────────────── list ──────────────────────────


def run_archive_list(
    *,
    con: sqlite3.Connection,
    policy: ArchivePolicy,
    machine_id: str,
    policy_name: str = "archive.yml",
) -> ArchiveListReport:
    """``restic snapshots`` per unique repository declared in the policy.

    Repositories are de-duplicated in policy-declaration order. List is
    a read operation; we still audit-log it so the chain captures who
    queried what, when.
    """
    repositories: "OrderedDict[str, None]" = OrderedDict()
    for src in policy.sources:
        if not src.enabled:
            continue
        repositories[src.repository] = None

    report = ArchiveListReport(
        policy_name=policy_name,
        repositories=list(repositories.keys()),
    )

    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-archive",
        action="archive_list_start",
        payload={
            "policy_name": policy_name,
            "repository_count": len(report.repositories),
        },
    )

    for repository in report.repositories:
        result = run_restic_snapshots(
            defaults=policy.defaults, repository=repository
        )
        if result.returncode == 0:
            for snap in result.snapshots:
                # Tag the snapshot with its repository so the merged
                # list stays useful when multiple repos are listed.
                snap_with_repo = dict(snap)
                snap_with_repo.setdefault("_repository", repository)
                report.snapshots.append(snap_with_repo)
        else:
            report.failures.append(
                {
                    "repository": repository,
                    "returncode": result.returncode,
                    "stderr_tail": result.stderr_tail,
                }
            )

    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-archive",
        action="archive_list_end",
        payload={
            "policy_name": policy_name,
            "repositories": report.repositories,
            "snapshot_count": len(report.snapshots),
            "failure_count": len(report.failures),
        },
    )
    return report


# ─────────────────────── init ──────────────────────────


def run_archive_init(
    *,
    con: sqlite3.Connection,
    policy: ArchivePolicy,
    machine_id: str,
    policy_name: str = "archive.yml",
) -> list[tuple[str, ResticRunResult]]:
    """``restic init`` per unique repository. One-time setup.

    Per ADR-0002, callers route through the CLI which requires
    ``--execute``. The runner itself doesn't gate — it's used directly
    by tests too.
    """
    repositories: "OrderedDict[str, None]" = OrderedDict()
    for src in policy.sources:
        if not src.enabled:
            continue
        repositories[src.repository] = None

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-archive",
        action="archive_init_start",
        payload={
            "policy_name": policy_name,
            "repository_count": len(repositories),
            "started_at": started,
        },
    )

    results: list[tuple[str, ResticRunResult]] = []
    for repository in repositories:
        result = run_restic_init(defaults=policy.defaults, repository=repository)
        results.append((repository, result))
        repo_audit.append(
            con,
            machine_id=machine_id,
            actor="steward-archive",
            action="archive_init_repo",
            payload={
                "policy_name": policy_name,
                "repository": repository,
                **_summarise_for_audit(result),
            },
        )

    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    successes = sum(1 for _, r in results if r.returncode == 0)
    failures = sum(1 for _, r in results if r.returncode != 0)
    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-archive",
        action="archive_init_end",
        payload={
            "policy_name": policy_name,
            "successes": successes,
            "failures": failures,
            "finished_at": finished,
        },
    )
    return results


__all__ = [
    "ArchiveListReport",
    "ArchiveSnapshotReport",
    "SnapshotReport",
    "run_archive_init",
    "run_archive_list",
    "run_archive_snapshot",
]
