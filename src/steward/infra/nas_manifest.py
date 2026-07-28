# SPDX-License-Identifier: Apache-2.0

"""Export ``nas_manifest`` apply rows for operator/DSM/SSH execution.

Backup and other read-only NAS tiers never receive direct Steward
filesystem mutation (ADR-0009 / NAS_READONLY_TIERS). The reconciler
emits ``nas_manifest`` rows; apply **records** them to a run-scoped
export file + audit log instead of silently skipping (pre-v0.3.13).

The operator (or a future SSH/DSM adapter) consumes the export. Steward
does not SSH to the NAS in this module.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from steward.core.model.manifest import ManifestRow
from steward.infra.db import repo_audit
from steward.infra.db.settings import data_dir


@dataclass(frozen=True)
class NasManifestExportResult:
    export_path: Path
    row_index: int
    dry_run: bool


def nas_export_path(manifest_run_id: str) -> Path:
    """Return ``<data_dir>/runs/<run_id>/nas_manifest.tsv``."""
    return data_dir() / "runs" / manifest_run_id / "nas_manifest.tsv"


def record_nas_manifest_row(
    *,
    con: sqlite3.Connection,
    row: ManifestRow,
    row_index: int,
    manifest_run_id: str,
    machine_id: str,
    dry_run: bool,
) -> NasManifestExportResult:
    """Append one NAS-tier instruction to the run export + audit log.

    On dry-run: no file write, no audit row (matches other actions'
    dry-run "plan only" posture for side effects outside the rolled-
    back transaction). Returns the would-be export path.
    """
    export = nas_export_path(manifest_run_id)
    payload: dict[str, Any] = {
        "row_index": row_index,
        "permanode_id": row.permanode_id,
        "canonical_hash": row.canonical_hash,
        "size_bytes": row.size_bytes,
        "source_path": row.source_path,
        "source_tier": row.source_tier,
        "destination_path": row.destination_path,
        "destination_tier": row.destination_tier,
        "rationale": row.rationale,
        "export_path": str(export),
        "dry_run": dry_run,
        "operator_next": (
            "Review export TSV; execute deletes on the NAS via DSM/SSH "
            "outside Steward; then re-scan the Backup tier."
        ),
    }

    if dry_run:
        return NasManifestExportResult(
            export_path=export, row_index=row_index, dry_run=True
        )

    export.parent.mkdir(parents=True, exist_ok=True)
    write_header = not export.exists()
    with export.open("a", encoding="utf-8") as fh:
        if write_header:
            fh.write(
                "row_index\tpermanode_id\tcanonical_hash\tsize_bytes\t"
                "source_path\tsource_tier\trationale\n"
            )
        fh.write(
            f"{row_index}\t{row.permanode_id}\t{row.canonical_hash}\t"
            f"{row.size_bytes}\t{row.source_path}\t{row.source_tier}\t"
            f"{(row.rationale or '').replace(chr(9), ' ').replace(chr(10), ' ')}\n"
        )

    # Touch a small sidecar README once per run.
    readme = export.parent / "README-nas-manifest.txt"
    if not readme.exists():
        readme.write_text(
            "Steward nas_manifest export\n"
            f"run_id={manifest_run_id}\n"
            f"written_at={datetime.now(timezone.utc).isoformat()}\n"
            "\n"
            "These rows target read-only NAS tiers. Steward does not delete\n"
            "them. Execute via DSM/SSH, then `steward scan --root <Backup>`.\n",
            encoding="utf-8",
        )

    repo_audit.append(
        con,
        machine_id=machine_id,
        actor="steward-apply",
        action="nas_manifest_exported",
        payload=payload,
        manifest_run_id=manifest_run_id,
        permanode_id=row.permanode_id if _permanode_exists(con, row.permanode_id) else None,
    )
    return NasManifestExportResult(
        export_path=export, row_index=row_index, dry_run=False
    )


def _permanode_exists(con: sqlite3.Connection, permanode_id: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM permanodes WHERE id = ?", (permanode_id,)
    ).fetchone()
    return row is not None


__all__ = [
    "NasManifestExportResult",
    "nas_export_path",
    "record_nas_manifest_row",
]
