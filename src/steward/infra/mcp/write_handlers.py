# SPDX-License-Identifier: Apache-2.0

"""Write-side MCP handlers — thin wrappers around the CLI orchestrators.

The v0.2.0 MCP server (``handlers.py``) is purely read-only. This module
adds the *write* surface: replication runs, archive snapshots + init,
stash finalize / restore. Per ADR-0002 (operator-in-the-loop), every
write handler:

1. Writes a single ``mcp_write_invoked`` audit entry **before** calling
   the orchestrator. This is the audit trail's record that "this
   mutation was driven by an MCP client" — separate from the
   orchestrator's own audit chain.
2. Delegates the actual work to the existing CLI orchestrator (which
   re-opens its own DB connection and writes its own audit chain).
3. Returns a JSON-friendly dict so the FastMCP framework can serialise
   directly.

Why the wrapping audit entry instead of changing every orchestrator's
``actor`` field? It keeps the orchestrator surface unchanged (CLI +
MCP share the same code path) while still letting downstream queries
distinguish MCP-invoked runs from CLI-invoked ones.

Tool annotations on the FastMCP side mark each write tool with
``destructiveHint=True``. Real clients (Claude Desktop, etc.) surface
this as a confirmation UI so the operator stays in the loop.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from steward.infra.archive.orchestrate import (
    resolve_policy_path as resolve_archive_policy_path,
)
from steward.infra.archive.orchestrate import (
    run_init as run_archive_init,
)
from steward.infra.archive.orchestrate import (
    run_snapshot as run_archive_snapshot,
)
from steward.infra.db import repo_audit
from steward.infra.db.admin import migrate, resolve_machine_id
from steward.infra.db.connect import connect
from steward.infra.db.settings import inventory_db_path
from steward.infra.db.stash_cmd import finalize_stash, restore_stash
from steward.infra.replicate.orchestrate import (
    resolve_policy_path as resolve_replicate_policy_path,
)
from steward.infra.replicate.orchestrate import (
    run_replicate,
)


def _ensure_db_and_machine() -> tuple[Path, str]:
    """Resolve the inventory DB path + machine_id, migrating if needed."""
    target = inventory_db_path()
    if not target.exists():
        migrate(target)
    return target, resolve_machine_id(target)


def _mcp_invoke_audit(
    *,
    db_path: Path,
    machine_id: str,
    tool: str,
    args: dict[str, Any],
) -> None:
    """Append the ``mcp_write_invoked`` row that flags this run as
    MCP-driven. Independent transaction from the orchestrator's chain
    so a failure in the orchestrator doesn't void this row."""
    con = connect(db_path)
    try:
        repo_audit.append(
            con,
            machine_id=machine_id,
            actor="steward-mcp",
            action="mcp_write_invoked",
            payload={"tool": tool, "args": args},
        )
        con.commit()
    finally:
        con.close()


# ─────────────────────── replication ──────────────────────────


def replicate_dry_run(*, policy: str = "replication.yml") -> dict[str, Any]:
    """Run ``steward replicate run --dry-run`` from MCP. Read-side
    semantics — rclone sees ``--dry-run`` so neither end mutates.
    Recorded with action=mcp_write_invoked for traceability."""
    db_path, machine_id = _ensure_db_and_machine()
    policy_path = resolve_replicate_policy_path(policy)
    _mcp_invoke_audit(
        db_path=db_path,
        machine_id=machine_id,
        tool="replicate_dry_run",
        args={"policy": policy},
    )
    report = run_replicate(
        db_path=db_path,
        policy_path=policy_path,
        machine_id=machine_id,
        dry_run=True,
    )
    return _serialise_replicate_report(report)


def replicate_execute(*, policy: str = "replication.yml") -> dict[str, Any]:
    """Run ``steward replicate run --execute`` from MCP. **Destructive** —
    mutates each replication target. Clients should surface a
    confirmation UI before invoking."""
    db_path, machine_id = _ensure_db_and_machine()
    policy_path = resolve_replicate_policy_path(policy)
    _mcp_invoke_audit(
        db_path=db_path,
        machine_id=machine_id,
        tool="replicate_execute",
        args={"policy": policy},
    )
    report = run_replicate(
        db_path=db_path,
        policy_path=policy_path,
        machine_id=machine_id,
        dry_run=False,
    )
    return _serialise_replicate_report(report)


def _serialise_replicate_report(report: Any) -> dict[str, Any]:
    """Convert :class:`ReplicationReport` to a JSON-friendly dict."""
    return {
        "policy_name": report.policy_name,
        "runs": report.runs,
        "successes": report.successes,
        "failures": report.failures,
        "skipped": report.skipped,
        "bytes_transferred": report.bytes_transferred,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "sources": [
            {
                "name": s.name,
                "source": s.source,
                "destination": s.destination,
                "mode": s.mode,
                "dry_run": s.dry_run,
                "skipped": s.skipped,
                "returncode": s.result.returncode if s.result else None,
                "duration_seconds": (
                    s.result.duration_seconds if s.result else None
                ),
                "stats": s.result.stats if s.result else {},
            }
            for s in report.sources
        ],
    }


# ─────────────────────── archive ──────────────────────────


def archive_snapshot_dry_run(
    *, policy: str = "archive.yml"
) -> dict[str, Any]:
    """``restic backup --dry-run`` for each source. Read-side semantics."""
    db_path, machine_id = _ensure_db_and_machine()
    policy_path = resolve_archive_policy_path(policy)
    _mcp_invoke_audit(
        db_path=db_path,
        machine_id=machine_id,
        tool="archive_snapshot_dry_run",
        args={"policy": policy},
    )
    report = run_archive_snapshot(
        db_path=db_path,
        policy_path=policy_path,
        machine_id=machine_id,
        dry_run=True,
    )
    return _serialise_archive_snapshot_report(report)


def archive_snapshot_execute(
    *, policy: str = "archive.yml"
) -> dict[str, Any]:
    """**Destructive** — writes new snapshots to restic repositories.
    Clients must surface confirmation UI."""
    db_path, machine_id = _ensure_db_and_machine()
    policy_path = resolve_archive_policy_path(policy)
    _mcp_invoke_audit(
        db_path=db_path,
        machine_id=machine_id,
        tool="archive_snapshot_execute",
        args={"policy": policy},
    )
    report = run_archive_snapshot(
        db_path=db_path,
        policy_path=policy_path,
        machine_id=machine_id,
        dry_run=False,
    )
    return _serialise_archive_snapshot_report(report)


def _serialise_archive_snapshot_report(report: Any) -> dict[str, Any]:
    """Convert :class:`ArchiveSnapshotReport` to a JSON-friendly dict."""
    return {
        "policy_name": report.policy_name,
        "runs": report.runs,
        "successes": report.successes,
        "failures": report.failures,
        "skipped": report.skipped,
        "total_bytes_added": report.total_bytes_added,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "sources": [
            {
                "name": s.name,
                "source": s.source,
                "repository": s.repository,
                "dry_run": s.dry_run,
                "skipped": s.skipped,
                "returncode": s.result.returncode if s.result else None,
                "snapshot_id": (
                    s.result.summary.get("snapshot_id")
                    if s.result
                    else None
                ),
                "data_added": (
                    s.result.summary.get("data_added", 0) if s.result else 0
                ),
            }
            for s in report.sources
        ],
    }


def archive_init_execute(*, policy: str = "archive.yml") -> dict[str, Any]:
    """**Destructive** — creates new encrypted restic repositories.
    One-time setup per repo; clients must surface confirmation UI."""
    db_path, machine_id = _ensure_db_and_machine()
    policy_path = resolve_archive_policy_path(policy)
    _mcp_invoke_audit(
        db_path=db_path,
        machine_id=machine_id,
        tool="archive_init_execute",
        args={"policy": policy},
    )
    results = run_archive_init(
        db_path=db_path,
        policy_path=policy_path,
        machine_id=machine_id,
    )
    return {
        "policy_name": policy_path.name,
        "repositories": [
            {
                "repository": repository,
                "returncode": result.returncode,
                "duration_seconds": result.duration_seconds,
                "stderr_tail": result.stderr_tail[-512:],
            }
            for repository, result in results
        ],
    }


# ─────────────────────── stash ──────────────────────────


def stash_finalize_execute(
    *,
    run_id: str,
    cooling_off_days: int = 7,
    force: bool = False,
) -> dict[str, Any]:
    """**Destructive** — permanently deletes stashed files for ``run_id``.
    Refuses entries younger than ``cooling_off_days`` unless ``force=True``.
    Clients must surface confirmation UI before calling."""
    db_path, machine_id = _ensure_db_and_machine()
    _mcp_invoke_audit(
        db_path=db_path,
        machine_id=machine_id,
        tool="stash_finalize_execute",
        args={
            "run_id": run_id,
            "cooling_off_days": cooling_off_days,
            "force": force,
        },
    )
    counts = finalize_stash(
        manifest_run_id=run_id,
        machine_id=machine_id,
        cooling_off_days=cooling_off_days,
        force=force,
        db_path=db_path,
    )
    return {
        "run_id": run_id,
        "finalized": int(counts.get("finalized", 0)),
        "skipped_young": int(counts.get("skipped_young", 0)),
        "errored": int(counts.get("errored", 0)),
    }


def stash_restore_execute(*, run_id: str) -> dict[str, Any]:
    """Move each stashed file BACK to its original location.

    Not strictly destructive (it's a recovery operation) but mutates
    the filesystem, so we still mark ``destructiveHint=True`` to keep
    the client-side confirmation flow consistent.
    """
    db_path, machine_id = _ensure_db_and_machine()
    _mcp_invoke_audit(
        db_path=db_path,
        machine_id=machine_id,
        tool="stash_restore_execute",
        args={"run_id": run_id},
    )
    counts = restore_stash(
        manifest_run_id=run_id,
        machine_id=machine_id,
        db_path=db_path,
    )
    return {
        "run_id": run_id,
        "restored": int(counts.get("restored", 0)),
        "skipped_occupied": int(counts.get("skipped_occupied", 0)),
        "errored": int(counts.get("errored", 0)),
    }


__all__ = [
    "archive_init_execute",
    "archive_snapshot_dry_run",
    "archive_snapshot_execute",
    "replicate_dry_run",
    "replicate_execute",
    "stash_finalize_execute",
    "stash_restore_execute",
]
