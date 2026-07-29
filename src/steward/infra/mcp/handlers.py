# SPDX-License-Identifier: Apache-2.0

"""MCP handlers — inventory query, plan dry-runs, and gated apply_execute.

Read helpers open SQLite with ``read_only=True``. Plan/execute helpers
may write plan TSVs, plan tokens, and audit rows (ADR-0011/0016).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from steward.infra.db.connect import connect


def _ro(db_path: Path) -> sqlite3.Connection:
    """Open a read-only connection. Centralised so a future change to
    pragmas / extension loading lands in one place."""
    return connect(db_path, read_only=True, load_vec=False)


# ─────────────────────── aggregate stats ──────────────────────────


def inventory_stats(db_path: Path, *, include_imports: bool = False) -> dict[str, Any]:
    """Return top-level counts: permanodes, claims, scan_runs, by-tier,
    by-domain. Acts as a "is the inventory alive" smoke test that
    LLM clients can poll cheaply.

    With ``include_imports=True`` (v0.3.6 / ADR-0013) the
    aggregates span local + every attached inventory's claims.
    """
    from steward.infra.stats import by_domain as _by_domain
    from steward.infra.stats import by_tier as _by_tier
    from steward.infra.stats import overview as _overview

    ov = _overview(db_path=db_path, include_imports=include_imports)
    tier_rows = _by_tier(db_path=db_path, include_imports=include_imports)
    domain_rows = _by_domain(db_path=db_path, include_imports=include_imports)

    # scan_runs + audit_entries stay local — those describe THIS
    # machine's pipeline, not the cross-machine claim universe.
    con = _ro(db_path)
    try:
        scan_runs = con.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0]
        audit_entries = con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    finally:
        con.close()

    return {
        "permanodes": int(ov.permanodes),
        "current_claims": int(ov.current_claims),
        "scan_runs": int(scan_runs),
        "audit_entries": int(audit_entries),
        "by_tier": {r.tier: r.claim_count for r in tier_rows},
        "by_domain": {(r.domain or "(none)"): r.claim_count for r in domain_rows},
        "include_imports": include_imports,
    }


# ─────────────────────── search by path / hash ──────────────────────────


def find_permanode_by_path(db_path: Path, *, path_substring: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return current claims whose ``file_path`` contains ``path_substring``.

    Matches are case-sensitive (the underlying SQLite collation default).
    Results are deterministic: ordered by permanode_id ASC then file_path ASC.
    """
    con = _ro(db_path)
    try:
        rows = con.execute(
            """
            SELECT c.permanode_id, p.canonical_hash, p.size_bytes,
                   c.file_path, c.tier, c.domain, c.classification
            FROM claims c
            JOIN permanodes p ON p.id = c.permanode_id
            WHERE c.is_current = 1 AND c.file_path LIKE ?
            ORDER BY c.permanode_id, c.file_path
            LIMIT ?
            """,
            (f"%{path_substring}%", int(limit)),
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "permanode_id": str(r[0]),
            "canonical_hash": str(r[1]),
            "size_bytes": int(r[2]),
            "file_path": str(r[3]),
            "tier": str(r[4]),
            "domain": (r[5] if r[5] is not None else None),
            "classification": (r[6] if r[6] is not None else None),
        }
        for r in rows
    ]


def find_permanode_by_hash(db_path: Path, *, hash_prefix: str, limit: int = 10) -> list[dict[str, Any]]:
    """Look up permanodes whose ``canonical_hash`` starts with the given prefix.

    Useful when the operator only has a partial hash from an audit row.
    """
    con = _ro(db_path)
    try:
        rows = con.execute(
            """
            SELECT id, canonical_hash, size_bytes, first_seen_at, last_seen_at
            FROM permanodes
            WHERE canonical_hash LIKE ?
            ORDER BY canonical_hash
            LIMIT ?
            """,
            (f"{hash_prefix}%", int(limit)),
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "permanode_id": str(r[0]),
            "canonical_hash": str(r[1]),
            "size_bytes": int(r[2]),
            "first_seen_at": str(r[3]),
            "last_seen_at": str(r[4]),
        }
        for r in rows
    ]


def get_permanode(db_path: Path, *, permanode_id: str, include_imports: bool = False) -> dict[str, Any]:
    """Return permanode header + all current claims + recent audit entries.

    The shape is consciously close to what ``steward inspect`` prints —
    LLM clients can use this to provide rich context without separate
    round-trips.

    With ``include_imports=True`` (v0.3.6) the lookup spans attached
    inventories; each claim/audit row gets a ``source`` field
    distinguishing local from attached.
    """
    if not include_imports:
        # Fast path — preserves v0.2 query plan.
        con = _ro(db_path)
        try:
            head = con.execute(
                "SELECT id, canonical_hash, size_bytes, first_seen_at, last_seen_at FROM permanodes WHERE id = ?",
                (permanode_id,),
            ).fetchone()
            if head is None:
                return {"permanode_id": permanode_id, "found": False}

            claims = [
                {
                    "claim_id": int(r[0]),
                    "machine_id": str(r[1]),
                    "file_path": str(r[2]),
                    "tier": str(r[3]),
                    "domain": r[4],
                    "classification": r[5],
                    "scan_run_id": int(r[6]),
                    "observed_at": str(r[7]),
                    "is_current": bool(r[8]),
                }
                for r in con.execute(
                    "SELECT id, machine_id, file_path, tier, domain, classification, "
                    "scan_run_id, observed_at, is_current "
                    "FROM claims WHERE permanode_id = ? ORDER BY observed_at DESC",
                    (permanode_id,),
                )
            ]

            audit = [
                {
                    "id": int(r[0]),
                    "timestamp": str(r[1]),
                    "actor": str(r[2]),
                    "action": str(r[3]),
                }
                for r in con.execute(
                    "SELECT id, timestamp, actor, action FROM audit_log "
                    "WHERE permanode_id = ? ORDER BY id DESC LIMIT 20",
                    (permanode_id,),
                )
            ]
        finally:
            con.close()

        return {
            "found": True,
            "permanode": {
                "permanode_id": str(head[0]),
                "canonical_hash": str(head[1]),
                "size_bytes": int(head[2]),
                "first_seen_at": str(head[3]),
                "last_seen_at": str(head[4]),
            },
            "claims": claims,
            "recent_audit": audit,
        }

    # Fan-out path: delegate to the inspect facade which already
    # implements the ATTACH + UNION ALL across schemas.
    from steward.infra.db.inspect import inspect as _inspect

    result = _inspect(permanode_id, include_imports=True)
    if result is None:
        return {"permanode_id": permanode_id, "found": False}

    return {
        "found": True,
        "permanode": {
            "permanode_id": result.permanode_id,
            "canonical_hash": result.canonical_hash,
            "size_bytes": result.size_bytes,
            "first_seen_at": result.first_seen_at,
            "last_seen_at": result.last_seen_at,
            "source": result.source,
        },
        "claims": [
            {
                "claim_id": int(c.get("id", 0) or 0),
                "machine_id": str(c.get("machine_id", "")),
                "file_path": str(c.get("file_path", "")),
                "tier": str(c.get("tier", "")),
                "domain": c.get("domain"),
                "classification": c.get("classification"),
                "observed_at": str(c.get("observed_at", "")),
                "is_current": bool(c.get("is_current", False)),
                "source": str(c.get("source", "local")),
            }
            for c in result.claims
        ],
        "recent_audit": [
            {
                "id": int(a.get("id", 0) or 0),
                "timestamp": str(a.get("timestamp", "")),
                "actor": str(a.get("actor", "")),
                "action": str(a.get("action", "")),
                "source": str(a.get("source", "local")),
            }
            for a in result.audit_rows
        ],
    }


# ─────────────────────── policy introspection ──────────────────────────


def list_policies() -> list[dict[str, str]]:
    """List the bundled policies under ``src/steward/policies/``.

    Returns each as ``{"name": "<file.yml>", "kind": "<RetentionPolicy>"}``.
    The "kind" is read from the YAML's top-level ``kind:`` line without
    parsing the rest of the file.
    """
    # Locate policies relative to this module so the bundled-into-wheel
    # layout works the same as the source-tree layout.
    here = Path(__file__).resolve().parents[2] / "policies"
    if not here.exists():  # pragma: no cover - layout invariant
        return []

    out: list[dict[str, str]] = []
    for policy in sorted(here.glob("*.yml")):
        kind = "(unknown)"
        try:
            with policy.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("kind:"):
                        kind = line.split(":", 1)[1].strip()
                        break
        except OSError:  # pragma: no cover - bundled files always readable
            pass
        out.append({"name": policy.name, "kind": kind})
    return out


def show_policy(*, name: str) -> dict[str, Any]:
    """Return the raw YAML of a bundled policy by filename.

    Refuses to traverse outside the bundled policies directory; rejects
    any ``name`` containing ``/`` or ``..``.
    """
    if "/" in name or ".." in name:
        return {"found": False, "error": "invalid policy name"}
    here = Path(__file__).resolve().parents[2] / "policies"
    candidate = here / name
    if not candidate.exists() or not candidate.is_file():
        return {"found": False, "name": name}
    return {
        "found": True,
        "name": name,
        "yaml": candidate.read_text(encoding="utf-8"),
    }


# ─────────────────────── scan runs + audit tail ──────────────────────────


def recent_scan_runs(db_path: Path, *, limit: int = 10) -> list[dict[str, Any]]:
    """Return the most-recent scan_runs with their summary counters."""
    con = _ro(db_path)
    try:
        rows = con.execute(
            "SELECT id, started_at, finished_at, machine_id, root_path, "
            "       workers, include_containers, files_walked, files_hashed, "
            "       files_skipped, bytes_hashed, errors, notes "
            "FROM scan_runs ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "id": int(r[0]),
            "started_at": str(r[1]),
            "finished_at": (str(r[2]) if r[2] is not None else None),
            "machine_id": str(r[3]),
            "root_path": str(r[4]),
            "workers": int(r[5]),
            "include_containers": bool(r[6]),
            "files_walked": int(r[7]),
            "files_hashed": int(r[8]),
            "files_skipped": int(r[9]),
            "bytes_hashed": int(r[10]),
            "errors": int(r[11]),
            "notes": (r[12] if r[12] is not None else None),
        }
        for r in rows
    ]


def tail_audit_log(
    db_path: Path,
    *,
    limit: int = 20,
    action: str | None = None,
) -> list[dict[str, Any]]:
    """Return the last ``limit`` audit_log rows, newest first.

    ``action`` (optional) narrows to one action kind (e.g. "scan_end",
    "stash", "promote") via exact string match.
    """
    con = _ro(db_path)
    try:
        if action is None:
            rows = con.execute(
                "SELECT id, timestamp, machine_id, actor, action, "
                "       payload_json, prev_hash, row_hash "
                "FROM audit_log ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id, timestamp, machine_id, actor, action, "
                "       payload_json, prev_hash, row_hash "
                "FROM audit_log WHERE action = ? ORDER BY id DESC LIMIT ?",
                (action, int(limit)),
            ).fetchall()
    finally:
        con.close()
    return [
        {
            "id": int(r[0]),
            "timestamp": str(r[1]),
            "machine_id": str(r[2]),
            "actor": str(r[3]),
            "action": str(r[4]),
            "payload": str(r[5]),
            "prev_hash": str(r[6]),
            "row_hash": str(r[7]),
        }
        for r in rows
    ]


def list_machines(db_path: Path, *, include_imports: bool = False) -> list[dict[str, Any]]:
    """List every machine_id that has touched the inventory, with
    counts + first/last seen.

    Wraps :func:`steward.infra.machines.list_machines` for the MCP
    surface. Returns JSON-friendly dicts. With
    ``include_imports=True`` (v0.3.6) attached inventories'
    machine_ids appear alongside, each tagged with ``source``.
    """
    from steward.infra.machines import list_machines as _list

    return [
        {
            "machine_id": s.machine_id,
            "is_current": s.is_current,
            "source": s.source,
            "claim_count": s.claim_count,
            "current_claim_count": s.current_claim_count,
            "scan_run_count": s.scan_run_count,
            "audit_entry_count": s.audit_entry_count,
            "first_seen_at": s.first_seen_at,
            "last_seen_at": s.last_seen_at,
        }
        for s in _list(db_path=db_path, include_imports=include_imports)
    ]


def get_machine(db_path: Path, *, machine_id: str, include_imports: bool = False) -> dict[str, Any]:
    """Full details for one machine_id + recent activity.

    Returns ``{"found": False, "machine_id": <input>}`` when nothing
    in the inventory references the id (matches the
    ``get_permanode`` shape). With ``include_imports=True`` (v0.3.6)
    the lookup spans attached inventories.
    """
    from steward.infra.machines import get_machine as _get

    details = _get(
        db_path=db_path,
        machine_id=machine_id,
        include_imports=include_imports,
    )
    if details is None:
        return {"found": False, "machine_id": machine_id}
    s = details.summary
    return {
        "found": True,
        "summary": {
            "machine_id": s.machine_id,
            "is_current": s.is_current,
            "source": s.source,
            "claim_count": s.claim_count,
            "current_claim_count": s.current_claim_count,
            "scan_run_count": s.scan_run_count,
            "audit_entry_count": s.audit_entry_count,
            "first_seen_at": s.first_seen_at,
            "last_seen_at": s.last_seen_at,
        },
        "recent_scan_runs": [
            {"kind": a.kind, "timestamp": a.timestamp, "summary": a.summary} for a in details.recent_scan_runs
        ],
        "recent_audit": [
            {"kind": a.kind, "timestamp": a.timestamp, "summary": a.summary} for a in details.recent_audit
        ],
    }


def policy_plan(
    *,
    policy: str = "retention.yml",
    out_path: str | None = None,
    root_prefix: str | None = None,
) -> dict[str, Any]:
    """Generate a plan manifest (read-only vs inventory; writes only the TSV).

    ``policy`` is a bundled name (e.g. ``retention.yml``) or absolute path.
    Does not apply the plan. Returns row counts by action.
    """
    from pathlib import Path as _Path

    from steward.infra.db.plan import plan as _plan
    from steward.infra.db.settings import data_dir

    here = Path(__file__).resolve().parents[2] / "policies"
    p = _Path(policy)
    if not p.exists():
        bundled = here / policy
        if not bundled.exists():
            return {"ok": False, "error": f"policy not found: {policy}"}
        p = bundled
    out = _Path(out_path) if out_path else (data_dir() / "runs" / "mcp-plan.tsv")
    try:
        summary = _plan(policy_path=p, out_path=out, root_prefix=root_prefix)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "policy_path": str(summary.policy_path),
        "out_path": str(summary.out_path),
        "rows": summary.rows,
        "stash_rows": summary.stash_rows,
        "retire_direct_rows": summary.retire_direct_rows,
        "nas_manifest_rows": summary.nas_manifest_rows,
        "promote_rows": summary.promote_rows,
        "manifest_run_id": summary.manifest_run_id,
    }


def apply_dry_run(
    *,
    manifest_path: str,
    max_files: int | None = None,
    skip_verify: bool = False,
    allow_store_path_unlink: bool = False,
    require_fp_healthy: bool = True,
    issue_plan_token: bool = True,
) -> dict[str, Any]:
    """Dry-run apply a plan TSV (ADR-0002). Never mutates the filesystem.

    Does not run ``--execute``. Returns applied/skipped/errored counts.
    On success with ``issue_plan_token=True``, returns a one-shot
    ``plan_token`` required by MCP ``apply_execute`` (ADR-0016).
    ``require_fp_healthy`` defaults True (match execute) so agents do
    not get a token that later fails FP preflight.
    """
    from pathlib import Path as _Path

    from steward.infra.db.admin import resolve_machine_id
    from steward.infra.db.apply import ApplyRefused, apply_manifest
    from steward.infra.db.settings import inventory_db_path
    from steward.infra.mcp.plan_tokens import issue_plan_token as _issue

    path = _Path(manifest_path)
    if not path.exists():
        return {"ok": False, "error": f"manifest not found: {manifest_path}"}
    prefer_mount = not allow_store_path_unlink
    if require_fp_healthy:
        from steward.infra.fp_preflight import (
            fp_health_problems,
            fp_health_warnings,
            manifest_needs_fp_health,
        )

        if manifest_needs_fp_health(path):
            problems = fp_health_problems(prefer_mount_unlink=prefer_mount)
            if problems:
                return {
                    "ok": False,
                    "fp_unhealthy": True,
                    "problems": list(problems),
                    "error": "require_fp_healthy: cloud-FP pre-flight failed",
                }
            warnings = fp_health_warnings(prefer_mount_unlink=prefer_mount)
        else:
            warnings = []
    else:
        warnings = []

    target = inventory_db_path()
    machine_id = resolve_machine_id(target)
    try:
        result = apply_manifest(
            manifest_path=path,
            machine_id=machine_id,
            dry_run=True,
            max_files=max_files,
            skip_verify=skip_verify,
            prefer_mount_unlink=prefer_mount,
        )
    except ApplyRefused as exc:
        return {
            "ok": False,
            "refused": True,
            "rejected": list(exc.result.rejected_imported_claims),
            "error": str(exc),
        }
    out: dict[str, Any] = {
        "ok": True,
        "dry_run": True,
        "manifest_run_id": result.manifest_run_id,
        "manifest_path": str(path.resolve()),
        "rows_total": result.rows_total,
        "rows_applied": result.rows_applied,
        "rows_skipped": result.rows_skipped,
        "rows_errored": result.rows_errored,
        "errors": list(result.errors)[:50],
        "nas_export_path": result.nas_export_path,
        "fp_warnings": list(warnings),
        "plan_token": None,
        "plan_token_expires_at": None,
    }
    if issue_plan_token and result.rows_errored == 0:
        rec = _issue(
            manifest_path=path,
            machine_id=machine_id,
            rows_total=result.rows_total,
            rows_applied=result.rows_applied,
            max_files=max_files,
            dry_run_errors=result.rows_errored,
        )
        out["plan_token"] = rec.token
        out["plan_token_expires_at"] = rec.expires_at
        out["note"] = (
            "plan_token authorizes one MCP apply_execute for this manifest "
            "digest (ADR-0016). STEWARD_MCP_MODE=write required."
        )
    elif issue_plan_token and result.rows_errored:
        out["note"] = "plan_token not issued because dry-run had row errors — fix errors and re-run apply_dry_run"
    return out


def apply_execute(
    *,
    manifest_path: str,
    plan_token: str,
    max_files: int,
    skip_verify: bool = False,
    allow_store_path_unlink: bool = False,
    require_fp_healthy: bool = True,
) -> dict[str, Any]:
    """Execute a plan after a successful dry-run plan_token (ADR-0016).

    Requires ``STEWARD_MCP_MODE=write``. ``max_files`` is mandatory and
    capped by ``STEWARD_MCP_MAX_FILES_CAP`` (default 50).
    """
    from pathlib import Path as _Path

    from steward.infra.db.admin import resolve_machine_id
    from steward.infra.db.apply import ApplyRefused, apply_manifest
    from steward.infra.db.settings import inventory_db_path
    from steward.infra.mcp.capability import (
        McpCapabilityError,
        McpMode,
        mcp_actor,
        mcp_max_files_cap,
        record_mcp_write_invoked,
        require_mode,
    )
    from steward.infra.mcp.plan_tokens import (
        PlanTokenError,
        consume_plan_token,
        validate_plan_token,
    )

    try:
        require_mode(McpMode.WRITE, tool="apply_execute")
    except McpCapabilityError as exc:
        return {"ok": False, "error": str(exc)}

    cap = mcp_max_files_cap()
    try:
        if max_files is None:
            raise ValueError("missing")
        max_files_i = int(max_files)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": "max_files is required and must be an integer >= 1",
        }
    if max_files_i < 1:
        return {
            "ok": False,
            "error": "max_files is required and must be >= 1 for MCP apply_execute",
        }
    if max_files_i > cap:
        return {
            "ok": False,
            "error": (f"max_files={max_files_i} exceeds STEWARD_MCP_MAX_FILES_CAP={cap}"),
        }

    path = _Path(manifest_path)
    if not path.exists():
        return {"ok": False, "error": f"manifest not found: {manifest_path}"}

    prefer_mount = not allow_store_path_unlink
    if require_fp_healthy:
        from steward.infra.fp_preflight import (
            fp_health_problems,
            fp_health_warnings,
            manifest_needs_fp_health,
        )

        if manifest_needs_fp_health(path):
            problems = fp_health_problems(prefer_mount_unlink=prefer_mount)
            if problems:
                return {
                    "ok": False,
                    "fp_unhealthy": True,
                    "problems": list(problems),
                    "error": "require_fp_healthy: cloud-FP pre-flight failed",
                }
            warnings = fp_health_warnings(prefer_mount_unlink=prefer_mount)
        else:
            warnings = []
    else:
        warnings = []

    target = inventory_db_path()
    machine_id = resolve_machine_id(target)
    try:
        rec = validate_plan_token(
            token=plan_token,
            manifest_path=path,
            machine_id=machine_id,
            max_files=max_files_i,
        )
    except PlanTokenError as exc:
        return {"ok": False, "error": str(exc)}

    record_mcp_write_invoked(
        db_path=target,
        machine_id=machine_id,
        tool="apply_execute",
        args={
            "manifest_path": str(path.resolve()),
            "max_files": max_files_i,
            "skip_verify": skip_verify,
            "allow_store_path_unlink": allow_store_path_unlink,
            "require_fp_healthy": require_fp_healthy,
            "plan_token_prefix": plan_token[:8] + "…",
            "dry_run_rows_applied": rec.rows_applied,
        },
    )

    try:
        result = apply_manifest(
            manifest_path=path,
            machine_id=machine_id,
            dry_run=False,
            max_files=max_files_i,
            skip_verify=skip_verify,
            prefer_mount_unlink=prefer_mount,
        )
    except ApplyRefused as exc:
        # Token not consumed — operator can retry after fixing attach state.
        return {
            "ok": False,
            "refused": True,
            "rejected": list(exc.result.rejected_imported_claims),
            "error": str(exc),
            "plan_token_retained": True,
        }

    # Consume only after apply returned (mutations may have occurred).
    try:
        consume_plan_token(token=plan_token)
    except PlanTokenError:
        pass  # race: concurrent consume; apply already completed

    return {
        "ok": True,
        "dry_run": False,
        "executed": True,
        "manifest_run_id": result.manifest_run_id,
        "manifest_path": str(path.resolve()),
        "rows_total": result.rows_total,
        "rows_applied": result.rows_applied,
        "rows_skipped": result.rows_skipped,
        "rows_errored": result.rows_errored,
        "errors": list(result.errors)[:50],
        "nas_export_path": result.nas_export_path,
        "fp_warnings": list(warnings),
        "actor": mcp_actor(),
        "max_files": max_files_i,
    }


def status_snapshot(
    *,
    quick: bool = True,
    include_imports: bool = False,
) -> dict[str, Any]:
    """Operator status report as JSON (wraps ``steward status``)."""
    from steward.infra.db.settings import inventory_db_path
    from steward.infra.status import collect_status, status_to_dict

    db = inventory_db_path()
    if not db.exists():
        return {"ok": False, "error": f"inventory missing: {db}"}
    report = collect_status(
        db_path=db,
        quick=quick,
        include_imports=include_imports,
    )
    out = status_to_dict(report)
    out["ok"] = True
    out["quick"] = quick
    return out


def scan_status(*, root: str | None = None, limit: int = 5) -> dict[str, Any]:
    """Latest scan_runs, optionally filtered by root_path prefix/exact."""
    from steward.infra.db.settings import inventory_db_path

    db = inventory_db_path()
    if not db.exists():
        return {"ok": False, "error": f"inventory missing: {db}"}
    con = _ro(db)
    try:
        if root:
            rows = con.execute(
                """
                SELECT id, root_path, started_at, finished_at, files_walked,
                       files_hashed, files_skipped, errors, workers
                FROM scan_runs
                WHERE root_path = ? OR root_path LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (root, root.rstrip("/") + "%", int(limit)),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT id, root_path, started_at, finished_at, files_walked,
                       files_hashed, files_skipped, errors, workers
                FROM scan_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
    finally:
        con.close()
    runs = [
        {
            "id": int(r[0]),
            "root_path": str(r[1]),
            "started_at": r[2],
            "finished_at": r[3],
            "files_walked": r[4],
            "files_hashed": r[5],
            "files_skipped": r[6],
            "errors": r[7],
            "workers": r[8],
            "in_progress": r[3] is None,
        }
        for r in rows
    ]
    return {
        "ok": True,
        "root_filter": root,
        "runs": runs,
        "any_in_progress": any(x["in_progress"] for x in runs),
    }


def inspect_target(
    target: str,
    *,
    audit_limit: int = 20,
    include_imports: bool = False,
) -> dict[str, Any]:
    """Inspect by path, permanode id, or hash (wraps ``steward inspect``)."""
    from steward.infra.db.inspect import inspect as _inspect

    result = _inspect(target, audit_limit=audit_limit, include_imports=include_imports)
    if result is None:
        return {"ok": False, "error": f"no match for {target!r}"}
    return {
        "ok": True,
        "permanode_id": result.permanode_id,
        "canonical_hash": result.canonical_hash,
        "canonical_hash_algo": result.canonical_hash_algo,
        "size_bytes": result.size_bytes,
        "first_seen_at": result.first_seen_at,
        "last_seen_at": result.last_seen_at,
        "claims": list(result.claims),
        "audit_rows": list(result.audit_rows),
        "source": result.source,
        "resolution_schema": result.resolution_schema,
    }


def fp_status() -> dict[str, Any]:
    """Lightweight Dropbox store/mount fork probe (no fileproviderctl dump)."""
    from steward.infra.fp_status import collect_fp_status, fp_status_to_dict

    return fp_status_to_dict(collect_fp_status())


def mcp_capability() -> dict[str, Any]:
    """Report current MCP mode, actor, and max_files cap (ADR-0016)."""
    from steward.infra.mcp.capability import (
        McpCapabilityError,
        mcp_actor,
        mcp_max_files_cap,
        mcp_mode_name,
    )

    try:
        mode = mcp_mode_name()
    except McpCapabilityError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "mode": mode,
        "actor": mcp_actor(),
        "max_files_cap": mcp_max_files_cap(),
        "env": {
            "STEWARD_MCP_MODE": "read|plan|write (default plan)",
            "STEWARD_MCP_ACTOR": "audit actor override",
            "STEWARD_MCP_MAX_FILES_CAP": "hard cap for apply_execute (default 50)",
        },
    }


__all__ = [
    "apply_dry_run",
    "apply_execute",
    "find_permanode_by_hash",
    "find_permanode_by_path",
    "fp_status",
    "get_machine",
    "get_permanode",
    "inspect_target",
    "inventory_stats",
    "list_machines",
    "list_policies",
    "mcp_capability",
    "policy_plan",
    "recent_scan_runs",
    "scan_status",
    "show_policy",
    "status_snapshot",
    "tail_audit_log",
]
