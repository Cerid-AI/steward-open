# SPDX-License-Identifier: Apache-2.0

"""Read-only handlers behind the MCP tool surface.

Each function opens its own read-only connection (cheap on SQLite WAL)
and returns a JSON-friendly dict so the FastMCP wrapper can serialise
straight through. The handlers are pure: same db_path + same args →
same output.

Connection cleanup is in a try/finally — a partial fetch never leaks a
connection. The connection is opened with ``read_only=True`` so even a
bug in a handler cannot mutate the DB.
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


def inventory_stats(
    db_path: Path, *, include_imports: bool = False
) -> dict[str, Any]:
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


def find_permanode_by_path(
    db_path: Path, *, path_substring: str, limit: int = 10
) -> list[dict[str, Any]]:
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


def find_permanode_by_hash(
    db_path: Path, *, hash_prefix: str, limit: int = 10
) -> list[dict[str, Any]]:
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


def get_permanode(
    db_path: Path, *, permanode_id: str, include_imports: bool = False
) -> dict[str, Any]:
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
                "SELECT id, canonical_hash, size_bytes, first_seen_at, last_seen_at "
                "FROM permanodes WHERE id = ?",
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


def list_machines(
    db_path: Path, *, include_imports: bool = False
) -> list[dict[str, Any]]:
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


def get_machine(
    db_path: Path, *, machine_id: str, include_imports: bool = False
) -> dict[str, Any]:
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
            {"kind": a.kind, "timestamp": a.timestamp, "summary": a.summary}
            for a in details.recent_scan_runs
        ],
        "recent_audit": [
            {"kind": a.kind, "timestamp": a.timestamp, "summary": a.summary}
            for a in details.recent_audit
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
) -> dict[str, Any]:
    """Dry-run apply a plan TSV (ADR-0002). Never mutates the filesystem.

    Does not run ``--execute``. Returns applied/skipped/errored counts.
    """
    from pathlib import Path as _Path

    from steward.infra.db.admin import resolve_machine_id
    from steward.infra.db.apply import ApplyRefused, apply_manifest
    from steward.infra.db.settings import inventory_db_path

    path = _Path(manifest_path)
    if not path.exists():
        return {"ok": False, "error": f"manifest not found: {manifest_path}"}
    target = inventory_db_path()
    machine_id = resolve_machine_id(target)
    try:
        result = apply_manifest(
            manifest_path=path,
            machine_id=machine_id,
            dry_run=True,
            max_files=max_files,
            skip_verify=skip_verify,
            prefer_mount_unlink=not allow_store_path_unlink,
        )
    except ApplyRefused as exc:
        return {
            "ok": False,
            "refused": True,
            "rejected": list(exc.result.rejected_imported_claims),
            "error": str(exc),
        }
    return {
        "ok": True,
        "dry_run": True,
        "manifest_run_id": result.manifest_run_id,
        "rows_total": result.rows_total,
        "rows_applied": result.rows_applied,
        "rows_skipped": result.rows_skipped,
        "rows_errored": result.rows_errored,
        "errors": list(result.errors)[:50],
        "nas_export_path": result.nas_export_path,
    }


def fp_status() -> dict[str, Any]:
    """Lightweight Dropbox store/mount fork probe (no fileproviderctl dump)."""
    from steward.infra.fp_status import collect_fp_status, fp_status_to_dict

    return fp_status_to_dict(collect_fp_status())


__all__ = [
    "apply_dry_run",
    "find_permanode_by_hash",
    "find_permanode_by_path",
    "fp_status",
    "get_machine",
    "get_permanode",
    "inventory_stats",
    "list_machines",
    "list_policies",
    "policy_plan",
    "recent_scan_runs",
    "show_policy",
    "tail_audit_log",
]
