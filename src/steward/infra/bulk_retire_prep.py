# SPDX-License-Identifier: Apache-2.0
"""Bulk dual-presence retire prep (filter + optional apply dry-run; no execute).

Operator pipeline for Dropbox cloud-retire hygiene (OPEN_DEVELOPMENT P5):
1. filter plan TSV → dual / store_only buckets
2. optionally ``apply --dry-run`` on plan-dual.tsv
3. **never** auto-execute (ADR-0002)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from steward.infra.dual_presence import (
    dual_presence_stats_to_dict,
    filter_plan_file,
    write_filtered_plans,
)


@dataclass(frozen=True, slots=True)
class BulkRetirePrepResult:
    out_dir: str
    stats_path: str
    dual_tsv: str | None
    bucket_paths: dict[str, str] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    dry_run_ok: bool | None = None
    dry_run_summary: str | None = None
    execute_blocked: bool = True
    notes: tuple[str, ...] = ()


def prepare_bulk_cloud_retire(
    *,
    manifest: Path,
    out_dir: Path,
    store_root: Path | None = None,
    mount_root: Path | None = None,
    limit: int = 0,
    path_col: str = "source_path",
    run_apply_dry_run: bool = False,
    require_fp_healthy: bool = True,
) -> BulkRetirePrepResult:
    """Filter + write artefacts; optionally dry-run apply on dual bucket.

    Always sets ``execute_blocked=True``. Callers must never pass through to
    ``apply --execute`` from this helper.
    """
    notes: list[str] = [
        "execute gated: use steward apply --manifest <dual.tsv> --execute only after review",
        "ADR-0002 / ADR-0015: prefer --require-fp-healthy on cloud retire execute",
    ]
    result = filter_plan_file(
        manifest,
        store_root=store_root,
        mount_root=mount_root,
        limit=limit,
        path_col=path_col,
        intent="cloud_retire",
    )
    if result.stats.counted == 0:
        raise ValueError("no data rows in manifest")

    artifacts = write_filtered_plans(result, out_dir=out_dir)
    dual_path = artifacts.bucket_paths.get("dual")
    stats = dual_presence_stats_to_dict(result.stats)
    dry_ok: bool | None = None
    dry_summary: str | None = None

    if run_apply_dry_run:
        if not dual_path or not Path(dual_path).is_file():
            notes.append("skip apply dry-run: no dual bucket TSV")
            dry_ok = False
            dry_summary = "no dual rows"
        else:
            from steward.infra.db.admin import resolve_machine_id
            from steward.infra.db.apply import ApplyRefused, apply_manifest
            from steward.infra.db.settings import inventory_db_path

            dual_path_final = dual_path
            if require_fp_healthy:
                from steward.infra.fp_preflight import (
                    fp_health_problems,
                    manifest_needs_fp_health,
                )

                if manifest_needs_fp_health(Path(dual_path_final)):
                    problems = fp_health_problems(prefer_mount_unlink=True)
                    if problems:
                        dry_ok = False
                        dry_summary = "fp_health refused: " + "; ".join(problems[:5])
                        notes.append(dry_summary)
            else:
                notes.append("fp health gate skipped (--no-require-fp-healthy)")

            if dry_ok is not False:
                try:
                    mid = resolve_machine_id(inventory_db_path())
                    ar = apply_manifest(
                        manifest_path=Path(dual_path_final),
                        machine_id=mid,
                        dry_run=True,
                    )
                    dry_ok = len(ar.errors) == 0 and ar.rows_errored == 0
                    dry_summary = (
                        f"dry_run total={ar.rows_total} applied={ar.rows_applied} "
                        f"skipped={ar.rows_skipped} errored={ar.rows_errored}"
                    )
                    notes.append(f"apply --dry-run on dual: {dry_summary}")
                    sidecar = Path(artifacts.out_dir) / "apply-dry-run-summary.json"
                    try:
                        payload = {
                            "dual_tsv": dual_path,
                            "summary": dry_summary,
                            "dry_run_ok": dry_ok,
                            "errors": list(ar.errors)[:50],
                            "require_fp_healthy": require_fp_healthy,
                        }
                        sidecar.write_text(json.dumps(payload, indent=2, default=str) + "\n")
                    except OSError:
                        pass
                except ApplyRefused as exc:
                    dry_ok = False
                    dry_summary = f"apply refused: {exc}"
                    notes.append(dry_summary)
                except Exception as exc:  # noqa: BLE001
                    dry_ok = False
                    dry_summary = f"apply dry-run failed: {exc}"
                    notes.append(dry_summary)

    notes.append(
        f"dual={result.stats.dual} store_only={result.stats.store_only} "
        f"conflict={result.stats.conflict_name_path}"
    )
    return BulkRetirePrepResult(
        out_dir=artifacts.out_dir,
        stats_path=artifacts.stats_path,
        dual_tsv=dual_path,
        bucket_paths=dict(artifacts.bucket_paths),
        stats=stats,
        dry_run_ok=dry_ok,
        dry_run_summary=dry_summary,
        execute_blocked=True,
        notes=tuple(notes),
    )


def bulk_retire_prep_to_dict(result: BulkRetirePrepResult) -> dict[str, Any]:
    return asdict(result)


__all__ = [
    "BulkRetirePrepResult",
    "bulk_retire_prep_to_dict",
    "prepare_bulk_cloud_retire",
]
