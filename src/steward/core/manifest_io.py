# SPDX-License-Identifier: Apache-2.0

"""Read / write Steward plan manifests (TSV-with-comment-header).

Format::

    # steward-manifest-v1
    # produced_by: steward 0.1.0
    # produced_at: 2026-05-16T12:34:56Z
    # policy: retention.yml
    # phase: dedup-retire-l2
    # manifest_run_id: 9f3c2b…
    action<TAB>permanode_id<TAB>canonical_hash<TAB>size_bytes<TAB>source_path<TAB>source_tier<TAB>destination_path<TAB>destination_tier<TAB>rationale
    stash<TAB>abc…<TAB>def…<TAB>1234<TAB>/path/from<TAB>L2<TAB>/path/stash/from<TAB>L2<TAB>dup-of-canonical

The header carries provenance + the ``manifest_run_id`` UUID; every
audit row produced by ``steward apply`` against this manifest carries
the same id, so the chain ties back.
"""
from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from steward.core.errors import ManifestError
from steward.core.model.manifest import (
    Manifest,
    ManifestHeader,
    ManifestRow,
)

MANIFEST_VERSION = "steward-manifest-v1"
_COLUMNS = (
    "action",
    "permanode_id",
    "canonical_hash",
    "size_bytes",
    "source_path",
    "source_tier",
    "destination_path",
    "destination_tier",
    "rationale",
)


def write_manifest(path: Path, manifest: Manifest) -> None:
    """Serialise ``manifest`` to ``path`` (overwrites)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    h = manifest.header
    lines: list[str] = [
        f"# {MANIFEST_VERSION}",
        f"# produced_by: steward {h.produced_by_steward_version}",
        f"# produced_at: {h.produced_at.isoformat(timespec='seconds')}",
        f"# policy: {h.policy_name}",
    ]
    if h.phase_name is not None:
        lines.append(f"# phase: {h.phase_name}")
    lines.append(f"# manifest_run_id: {h.manifest_run_id}")
    text = "\n".join(lines) + "\n"

    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(text)
        w = csv.writer(f, dialect="excel-tab")
        w.writerow(_COLUMNS)
        for row in manifest.rows:
            w.writerow([
                row.action,
                row.permanode_id,
                row.canonical_hash,
                row.size_bytes,
                row.source_path,
                row.source_tier,
                row.destination_path or "",
                row.destination_tier or "",
                row.rationale,
            ])


def read_manifest(path: Path) -> Manifest:
    """Parse ``path`` into a :class:`Manifest`. Raises :class:`ManifestError`
    on malformed input.
    """
    if not path.exists():
        raise ManifestError(f"Manifest not found: {path}")
    header_lines: list[str] = []
    data_lines: list[str] = []
    saw_version = False
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("#"):
                header_lines.append(line)
                if MANIFEST_VERSION in line:
                    saw_version = True
            else:
                data_lines.append(line)

    if not saw_version:
        raise ManifestError(
            f"Manifest header missing version marker '{MANIFEST_VERSION}': {path}"
        )

    meta = _parse_header(header_lines)
    if not data_lines or not data_lines[0]:
        raise ManifestError(f"Manifest has no data rows: {path}")

    reader = csv.reader(data_lines, dialect="excel-tab")
    cols = next(reader)
    if tuple(cols) != _COLUMNS:
        raise ManifestError(
            f"Manifest columns {tuple(cols)} != expected {_COLUMNS}"
        )

    rows: list[ManifestRow] = []
    for row_cells in reader:
        if not row_cells:
            continue
        rec = dict(zip(cols, row_cells, strict=False))
        rows.append(
            ManifestRow(
                action=rec["action"],  # type: ignore[arg-type]
                permanode_id=rec["permanode_id"],
                canonical_hash=rec["canonical_hash"],
                size_bytes=int(rec["size_bytes"]),
                source_path=rec["source_path"],
                source_tier=rec["source_tier"],
                destination_path=rec["destination_path"] or None,
                destination_tier=rec["destination_tier"] or None,
                rationale=rec["rationale"],
            )
        )

    header = ManifestHeader(
        produced_by_steward_version=meta.get("produced_by", "unknown").replace("steward ", ""),
        produced_at=_parse_iso(meta.get("produced_at", "1970-01-01T00:00:00")),
        policy_name=meta.get("policy", "unknown"),
        phase_name=meta.get("phase"),
        manifest_run_id=meta.get("manifest_run_id", "unknown"),
    )
    return Manifest(header=header, rows=tuple(rows))


def _parse_header(lines: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        stripped = line.lstrip("#").strip()
        if not stripped or stripped == MANIFEST_VERSION:
            continue
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        out[key.strip()] = val.strip()
    return out


def _parse_iso(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.now(timezone.utc)
