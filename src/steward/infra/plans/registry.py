# SPDX-License-Identifier: Apache-2.0

"""Data-dir plan backlog registry (ADR-0019).

Registration writes only under the Steward data directory (TSV copy +
index). It is **not** a tier FS mutation and does not require
``--execute``. Prune of plan artefacts uses ``--dry-run|--execute``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from steward.core.manifest_io import read_manifest
from steward.core.plans.blocked import (
    HARD_BLOCKED_REASONS,
    evaluate_plan_blocked_reasons,
)
from steward.core.plans.model import (
    DryRunDigest,
    PlanBacklogRecord,
    PlanFilters,
    PlanPolicyRef,
    PlanStatus,
    plan_record_to_compact_dict,
    plan_record_to_dict,
)
from steward.infra.observability.swallowed import log_swallowed_error

INDEX_FILENAME = "index.jsonl"
LATEST_FILENAME = "LATEST"
BY_ID_DIRNAME = "by-id"
SUMMARY_FILENAME = "summary.json"
PLAN_TSV_FILENAME = "plan.tsv"
DRY_RUN_FILENAME = "dry_run.json"
FILTER_STATS_FILENAME = "filter-stats.json"


def plans_dir(data_dir: Path | None = None) -> Path:
    """Return ``<data_dir>/plans``."""
    if data_dir is None:
        from steward.infra.db.settings import data_dir as _data_dir

        data_dir = _data_dir()
    return Path(data_dir) / "plans"


def _by_id_dir(data_dir: Path, plan_id: str) -> Path:
    return plans_dir(data_dir) / BY_ID_DIRNAME / plan_id


def _ensure_plans_tree(data_dir: Path) -> Path:
    pdir = plans_dir(data_dir)
    pdir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(pdir, 0o700)
    except OSError as exc:
        log_swallowed_error(
            "plans.registry.chmod_dir",
            exc,
            context={"path": str(pdir)},
        )
    (pdir / BY_ID_DIRNAME).mkdir(parents=True, exist_ok=True)
    return pdir


def _sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError as exc:
        log_swallowed_error(
            "plans.registry.sha256",
            exc,
            context={"path": str(path)},
        )
        return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def register_plan_from_manifest(
    manifest_path: Path,
    *,
    data_dir: Path | None = None,
    policy_name: str | None = None,
    policy_path: str | None = None,
    policy_kind: str | None = None,
    machine_id: str | None = None,
    root_prefix: str | None = None,
    phase_name: str | None = None,
    max_files: int | None = None,
    cloud_retire_ready: bool | None = None,
    mcp_max_files_cap: int | None = None,
    inventory_stale: bool = False,
    copy_manifest: bool = True,
    notes: tuple[str, ...] = (),
    parent_plan_id: str | None = None,
) -> PlanBacklogRecord:
    """Register a plan TSV as a backlog object under the data dir.

    Computes action_counts + estimated_bytes from the manifest, evaluates
    blocked reasons, writes ``by-id/<plan_id>/`` + appends index.jsonl.
    """
    from steward.infra.db.settings import data_dir as _default_data_dir

    ddir = Path(data_dir) if data_dir is not None else _default_data_dir()
    _ensure_plans_tree(ddir)

    src = Path(manifest_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"manifest not found: {src}")

    manifest = read_manifest(src)
    plan_id = manifest.header.manifest_run_id
    if not plan_id or plan_id == "unknown":
        raise ValueError("manifest has no usable manifest_run_id")

    action_counts: dict[str, int] = dict(Counter(str(r.action) for r in manifest.rows))
    estimated_bytes = sum(int(r.size_bytes) for r in manifest.rows)
    rows_total = len(manifest.rows)

    dest_dir = _by_id_dir(ddir, plan_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_tsv = dest_dir / PLAN_TSV_FILENAME

    if copy_manifest:
        try:
            if src.resolve() != dest_tsv.resolve():
                shutil.copy2(src, dest_tsv)
        except OSError:
            # Fall back to reading bytes if copy2 fails (cross-device etc.)
            dest_tsv.write_bytes(src.read_bytes())
    else:
        # Symlink or just record external path — still keep a copy for durability.
        if not dest_tsv.exists():
            try:
                shutil.copy2(src, dest_tsv)
            except OSError:
                dest_tsv.write_bytes(src.read_bytes())

    manifest_path_stored = str(dest_tsv)
    manifest_sha = _sha256_file(dest_tsv)

    filter_stats = dest_dir / FILTER_STATS_FILENAME
    has_filter = filter_stats.is_file()

    sample_paths = [r.source_path for r in manifest.rows[:32]]

    blocked = evaluate_plan_blocked_reasons(
        rows_total=rows_total,
        action_counts=action_counts,
        estimated_bytes=estimated_bytes,
        manifest_exists=dest_tsv.is_file(),
        dry_run_errors=None,
        cloud_retire_ready=cloud_retire_ready,
        has_dual_presence_filter=has_filter,
        sample_source_paths=sample_paths,
        mcp_max_files_cap=mcp_max_files_cap,
        inventory_stale=inventory_stale,
    )

    status: PlanStatus = "blocked" if (set(blocked) & HARD_BLOCKED_REASONS) else "registered"

    pol_name = policy_name or manifest.header.policy_name
    pol_path = policy_path or pol_name
    pol_kind = policy_kind or "unknown"
    mid = machine_id or "unknown"

    # Prefer resolved machine_id when available.
    if machine_id is None:
        try:
            from steward.infra.db.admin import resolve_machine_id
            from steward.infra.db.settings import inventory_db_path

            mid = resolve_machine_id(inventory_db_path())
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "plans.registry.machine_id",
                exc,
                context={},
            )
            mid = "unknown"

    phase = phase_name if phase_name is not None else manifest.header.phase_name

    record = PlanBacklogRecord(
        plan_id=plan_id,
        created_at=_utc_now_iso(),
        machine_id=mid,
        policy=PlanPolicyRef(name=pol_name, path=pol_path, kind=pol_kind),
        filters=PlanFilters(
            root_prefix=root_prefix,
            phase_name=phase,
            max_files=max_files,
        ),
        action_counts=action_counts,
        rows_total=rows_total,
        estimated_bytes=estimated_bytes,
        blocked_reasons=blocked,
        status=status,
        manifest_path=manifest_path_stored,
        manifest_sha256=manifest_sha,
        dry_run=None,
        notes=notes,
        parent_plan_id=parent_plan_id,
        filter_stats_path=str(filter_stats) if has_filter else None,
    )

    _write_summary(dest_dir, record)
    _append_index(ddir, record)
    _write_latest(ddir, plan_id)
    return record


def _write_summary(dest_dir: Path, record: PlanBacklogRecord) -> None:
    path = dest_dir / SUMMARY_FILENAME
    payload = plan_record_to_dict(record)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _append_index(data_dir: Path, record: PlanBacklogRecord) -> None:
    path = plans_dir(data_dir) / INDEX_FILENAME
    line = json.dumps(plan_record_to_compact_dict(record), sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _write_latest(data_dir: Path, plan_id: str) -> None:
    path = plans_dir(data_dir) / LATEST_FILENAME
    try:
        path.write_text(plan_id + "\n", encoding="utf-8")
    except OSError as exc:
        log_swallowed_error(
            "plans.registry.write_latest",
            exc,
            context={"path": str(path)},
        )


def list_plans(
    *,
    data_dir: Path | None = None,
    status: str | None = None,
    policy: str | None = None,
    limit: int = 50,
) -> list[PlanBacklogRecord]:
    """List plans from index (last-writer-wins by plan_id), newest first.

    Falls back to scanning ``by-id/*/summary.json`` when index is empty.
    """
    from steward.infra.db.settings import data_dir as _default_data_dir

    ddir = Path(data_dir) if data_dir is not None else _default_data_dir()
    limit = max(1, min(int(limit), 500))

    by_id: dict[str, dict[str, Any]] = {}
    index_path = plans_dir(ddir) / INDEX_FILENAME
    if index_path.is_file():
        try:
            with index_path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        log_swallowed_error(
                            "plans.registry.index_json",
                            exc,
                            context={"path": str(index_path)},
                        )
                        continue
                    pid = str(row.get("plan_id") or "")
                    if pid:
                        by_id[pid] = row
        except OSError as exc:
            log_swallowed_error(
                "plans.registry.read_index",
                exc,
                context={"path": str(index_path)},
            )

    # Prefer full summary.json when present.
    records: list[PlanBacklogRecord] = []
    for pid in by_id:
        rec = show_plan(pid, data_dir=ddir)
        if rec is not None:
            records.append(rec)
        else:
            compact = by_id[pid]
            records.append(_record_from_compact(compact))

    # Scan by-id for any not in index.
    by_id_root = plans_dir(ddir) / BY_ID_DIRNAME
    if by_id_root.is_dir():
        seen = {r.plan_id for r in records}
        for child in by_id_root.iterdir():
            if not child.is_dir() or child.name in seen:
                continue
            rec = show_plan(child.name, data_dir=ddir)
            if rec is not None:
                records.append(rec)

    if status:
        records = [r for r in records if r.status == status]
    if policy:
        records = [r for r in records if r.policy.name == policy or policy in r.policy.path]

    records.sort(key=lambda r: r.created_at, reverse=True)
    return records[:limit]


def show_plan(plan_id: str, *, data_dir: Path | None = None) -> PlanBacklogRecord | None:
    """Load full ``summary.json`` for ``plan_id``, or None if missing."""
    from steward.infra.db.settings import data_dir as _default_data_dir

    ddir = Path(data_dir) if data_dir is not None else _default_data_dir()
    summary = _by_id_dir(ddir, plan_id) / SUMMARY_FILENAME
    if not summary.is_file():
        return None
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log_swallowed_error(
            "plans.registry.read_summary",
            exc,
            context={"path": str(summary)},
        )
        return None
    return _record_from_dict(data)


def refresh_plan_status(
    plan_id: str,
    *,
    data_dir: Path | None = None,
    db_path: Path | None = None,
) -> PlanBacklogRecord:
    """Recompute status from audit_log + dry_run sidecar; no tier mutation.

    Audit remains the forensic authority. Best-effort: missing DB yields
    status from local sidecars only.
    """
    from steward.infra.db.settings import data_dir as _default_data_dir
    from steward.infra.db.settings import inventory_db_path

    ddir = Path(data_dir) if data_dir is not None else _default_data_dir()
    rec = show_plan(plan_id, data_dir=ddir)
    if rec is None:
        raise FileNotFoundError(f"plan not found: {plan_id}")

    dest_dir = _by_id_dir(ddir, plan_id)
    manifest_path = Path(rec.manifest_path)
    manifest_exists = manifest_path.is_file()

    dry_run = rec.dry_run
    dry_path = dest_dir / DRY_RUN_FILENAME
    if dry_path.is_file():
        try:
            d = json.loads(dry_path.read_text(encoding="utf-8"))
            dry_run = DryRunDigest(
                ok=bool(d.get("ok", False)),
                errors=int(d.get("errors") or 0),
                applied=int(d.get("applied") or 0),
                skipped=int(d.get("skipped") or 0),
                at=d.get("at"),
                message=d.get("message"),
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            log_swallowed_error(
                "plans.registry.read_dry_run",
                exc,
                context={"path": str(dry_path)},
            )

    dry_errors = dry_run.errors if dry_run is not None else None
    filter_stats = dest_dir / FILTER_STATS_FILENAME
    has_filter = filter_stats.is_file()

    blocked = evaluate_plan_blocked_reasons(
        rows_total=rec.rows_total,
        action_counts=rec.action_counts,
        estimated_bytes=rec.estimated_bytes,
        manifest_exists=manifest_exists,
        dry_run_errors=dry_errors,
        cloud_retire_ready=None,
        has_dual_presence_filter=has_filter,
        sample_source_paths=None,
        mcp_max_files_cap=None,
        inventory_stale=False,
    )

    status: PlanStatus = rec.status
    if set(blocked) & HARD_BLOCKED_REASONS:
        status = "blocked"
    elif dry_run is not None:
        status = "dry_run_ok" if dry_run.ok and dry_run.errors == 0 else "dry_run_failed"

    # Audit-derived applied / partially_applied
    target_db = Path(db_path) if db_path is not None else inventory_db_path()
    if target_db.is_file():
        try:
            applied_status = _status_from_audit(target_db, plan_id, rows_total=rec.rows_total)
            if applied_status is not None:
                status = applied_status
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "plans.registry.audit_status",
                exc,
                context={"plan_id": plan_id, "db": str(target_db)},
            )

    updated = PlanBacklogRecord(
        plan_id=rec.plan_id,
        created_at=rec.created_at,
        machine_id=rec.machine_id,
        policy=rec.policy,
        filters=rec.filters,
        action_counts=rec.action_counts,
        rows_total=rec.rows_total,
        estimated_bytes=rec.estimated_bytes,
        blocked_reasons=blocked,
        status=status,
        manifest_path=rec.manifest_path,
        manifest_sha256=rec.manifest_sha256,
        dry_run=dry_run,
        notes=rec.notes,
        parent_plan_id=rec.parent_plan_id,
        filter_stats_path=str(filter_stats) if has_filter else None,
    )
    _write_summary(dest_dir, updated)
    _append_index(ddir, updated)
    return updated


def _status_from_audit(db_path: Path, plan_id: str, *, rows_total: int) -> PlanStatus | None:
    """Derive applied / partially_applied from audit rows for manifest_run_id."""
    from steward.infra.db.connect import connect

    con = connect(db_path, read_only=True, load_vec=False)
    try:
        cur = con.execute(
            """
            SELECT action, COUNT(*) AS n
            FROM audit_log
            WHERE manifest_run_id = ?
            GROUP BY action
            """,
            (plan_id,),
        )
        counts = {str(row[0]): int(row[1]) for row in cur.fetchall()}
    finally:
        con.close()

    if not counts:
        return None

    # Apply lifecycle markers.
    if "apply_end" in counts or any(a.endswith("_ok") for a in counts):
        # Count successful mutations loosely.
        success_actions = {
            "stash",
            "retire_direct",
            "promote",
            "nas_manifest",
            "stash_ok",
            "retire_direct_ok",
            "promote_ok",
        }
        n_ok = sum(v for k, v in counts.items() if k in success_actions or k.endswith("_ok"))
        if rows_total > 0 and n_ok >= rows_total:
            return "applied"
        if n_ok > 0:
            return "partially_applied"
        if "apply_end" in counts:
            return "applied"
    if "apply_start" in counts:
        return "partially_applied"
    return None


def write_dry_run_sidecar(
    plan_id: str,
    digest: DryRunDigest | dict[str, Any],
    *,
    data_dir: Path | None = None,
) -> Path | None:
    """Write dry_run.json under by-id/<plan_id>/; return path or None."""
    from steward.infra.db.settings import data_dir as _default_data_dir

    ddir = Path(data_dir) if data_dir is not None else _default_data_dir()
    dest_dir = _by_id_dir(ddir, plan_id)
    if not dest_dir.is_dir():
        return None
    if isinstance(digest, DryRunDigest):
        payload = {
            "ok": digest.ok,
            "errors": digest.errors,
            "applied": digest.applied,
            "skipped": digest.skipped,
            "at": digest.at or _utc_now_iso(),
            "message": digest.message,
        }
    else:
        payload = dict(digest)
        payload.setdefault("at", _utc_now_iso())
    path = dest_dir / DRY_RUN_FILENAME
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def prune_plans(
    *,
    data_dir: Path | None = None,
    older_than_days: int = 90,
    execute: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Prune old plan by-id dirs (data-dir only). Requires dry_run or execute.

    Returns summary dict with would_remove / removed plan_ids.
    """
    from steward.infra.db.settings import data_dir as _default_data_dir

    if not dry_run and not execute:
        raise ValueError("prune_plans requires dry_run=True or execute=True (ADR-0002)")

    ddir = Path(data_dir) if data_dir is not None else _default_data_dir()
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(older_than_days))
    candidates: list[str] = []

    for rec in list_plans(data_dir=ddir, limit=500):
        try:
            created = datetime.fromisoformat(rec.created_at.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if created < cutoff:
            candidates.append(rec.plan_id)

    removed: list[str] = []
    if execute:
        for pid in candidates:
            dest = _by_id_dir(ddir, pid)
            try:
                if dest.is_dir():
                    shutil.rmtree(dest)
                    removed.append(pid)
            except OSError as exc:
                log_swallowed_error(
                    "plans.registry.prune",
                    exc,
                    context={"plan_id": pid},
                )

    return {
        "ok": True,
        "older_than_days": older_than_days,
        "execute": execute,
        "dry_run": dry_run and not execute,
        "would_remove": candidates if not execute else [],
        "removed": removed,
        "count": len(removed) if execute else len(candidates),
    }


def _record_from_dict(data: dict[str, Any]) -> PlanBacklogRecord:
    pol = data.get("policy") or {}
    if isinstance(pol, str):
        policy = PlanPolicyRef(name=pol, path=pol, kind=str(data.get("policy_kind") or "unknown"))
    else:
        policy = PlanPolicyRef(
            name=str(pol.get("name") or "unknown"),
            path=str(pol.get("path") or pol.get("name") or "unknown"),
            kind=str(pol.get("kind") or "unknown"),
        )
    filt = data.get("filters") or {}
    filters = PlanFilters(
        root_prefix=filt.get("root_prefix"),
        phase_name=filt.get("phase_name"),
        max_files=filt.get("max_files"),
    )
    dry_raw = data.get("dry_run")
    dry: DryRunDigest | None = None
    if isinstance(dry_raw, dict):
        dry = DryRunDigest(
            ok=bool(dry_raw.get("ok", False)),
            errors=int(dry_raw.get("errors") or 0),
            applied=int(dry_raw.get("applied") or 0),
            skipped=int(dry_raw.get("skipped") or 0),
            at=dry_raw.get("at"),
            message=dry_raw.get("message"),
        )
    status_raw = str(data.get("status") or "registered")
    status: PlanStatus = status_raw  # type: ignore[assignment]
    return PlanBacklogRecord(
        plan_id=str(data.get("plan_id") or ""),
        created_at=str(data.get("created_at") or ""),
        machine_id=str(data.get("machine_id") or "unknown"),
        policy=policy,
        filters=filters,
        action_counts={str(k): int(v) for k, v in (data.get("action_counts") or {}).items()},
        rows_total=int(data.get("rows_total") or 0),
        estimated_bytes=int(data.get("estimated_bytes") or 0),
        blocked_reasons=tuple(str(x) for x in (data.get("blocked_reasons") or ())),
        status=status,
        manifest_path=str(data.get("manifest_path") or ""),
        manifest_sha256=data.get("manifest_sha256"),
        dry_run=dry,
        notes=tuple(str(x) for x in (data.get("notes") or ())),
        parent_plan_id=data.get("parent_plan_id"),
        filter_stats_path=data.get("filter_stats_path"),
    )


def _record_from_compact(data: dict[str, Any]) -> PlanBacklogRecord:
    return PlanBacklogRecord(
        plan_id=str(data.get("plan_id") or ""),
        created_at=str(data.get("created_at") or ""),
        machine_id=str(data.get("machine_id") or "unknown"),
        policy=PlanPolicyRef(
            name=str(data.get("policy") or "unknown"),
            path=str(data.get("policy") or "unknown"),
            kind=str(data.get("policy_kind") or "unknown"),
        ),
        filters=PlanFilters(),
        action_counts={str(k): int(v) for k, v in (data.get("action_counts") or {}).items()},
        rows_total=int(data.get("rows_total") or 0),
        estimated_bytes=int(data.get("estimated_bytes") or 0),
        blocked_reasons=tuple(str(x) for x in (data.get("blocked_reasons") or ())),
        status=str(data.get("status") or "registered"),  # type: ignore[arg-type]
        manifest_path=str(data.get("manifest_path") or ""),
        manifest_sha256=None,
        dry_run=None,
        notes=(),
    )


__all__ = [
    "list_plans",
    "plans_dir",
    "prune_plans",
    "refresh_plan_status",
    "register_plan_from_manifest",
    "show_plan",
    "write_dry_run_sidecar",
]
