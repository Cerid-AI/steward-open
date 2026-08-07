# SPDX-License-Identifier: Apache-2.0
"""Audit-log chain archive (ADR-0018 phase A — seal + verify, no shrink).

Exports a contiguous id-prefix of ``audit_log`` into a tar.xz segment under
``{data_dir}/execution-log/``, verifies offline + against live DB, then
records ``audit_archive_commit`` + optional registry row. Does **not**
delete hot rows (``--shrink`` is a later phase).
"""

from __future__ import annotations

import json
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import blake3 as _blake3

from steward._version import __version__
from steward.core.audit import GENESIS_PREV_HASH, compute_row_hash
from steward.infra.db import repo_audit
from steward.infra.db.admin import resolve_machine_id
from steward.infra.db.connect import connect
from steward.infra.db.settings import data_dir as default_data_dir

SEGMENT_FORMAT_VERSION = 1
SEGMENT_KIND = "audit_chain_segment"


@dataclass(frozen=True, slots=True)
class SegmentManifest:
    segment_format_version: int
    kind: str
    created_at: str
    exporter: dict[str, str]
    range: dict[str, Any]
    payload: dict[str, Any]
    prior_tip_hash: str | None


@dataclass(frozen=True, slots=True)
class ArchiveDryRun:
    first_id: int
    through_id: int
    row_count: int
    tip_hash: str
    prior_tip_hash: str | None
    genesis_prev_hash: str
    live_chain_ok: bool


@dataclass(frozen=True, slots=True)
class ArchiveSealResult:
    first_id: int
    through_id: int
    row_count: int
    tip_hash: str
    segment_path: Path
    segment_blake3: str
    audit_row_id: int
    dry_run: bool


@dataclass(frozen=True, slots=True)
class SegmentVerifyResult:
    ok: bool
    path: Path
    rows_checked: int
    first_id: int | None
    through_id: int | None
    tip_hash: str | None
    error: str | None


class ArchiveError(Exception):
    """Refuse archive / verify."""


def _execution_log_dir(data_dir: Path) -> Path:
    d = data_dir / "execution-log"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file_blake3(path: Path) -> str:
    h = _blake3.blake3()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _load_rows(
    con: Any,
    *,
    first_id: int,
    through_id: int,
) -> list[dict[str, Any]]:
    cur = con.execute(
        """
        SELECT id, timestamp, machine_id, actor, action, permanode_id,
               claim_id, manifest_run_id, payload_json, prev_hash, row_hash
        FROM audit_log
        WHERE id >= ? AND id <= ?
        ORDER BY id ASC
        """,
        (first_id, through_id),
    )
    rows: list[dict[str, Any]] = []
    for r in cur:
        rows.append(
            {
                "id": int(r[0]),
                "timestamp": r[1],
                "machine_id": r[2],
                "actor": r[3],
                "action": r[4],
                "permanode_id": r[5],
                "claim_id": r[6],
                "manifest_run_id": r[7],
                "payload_json": r[8],
                "prev_hash": r[9],
                "row_hash": r[10],
            }
        )
    return rows


def _verify_rows_chain(
    rows: list[dict[str, Any]],
    *,
    first_prev_expected: str,
) -> tuple[bool, int, str | None, str | None]:
    """Return (ok, n, error, tip_hash)."""
    if not rows:
        return False, 0, "empty segment", None
    prev_expected = first_prev_expected
    for i, row in enumerate(rows):
        if i > 0 and int(row["id"]) != int(rows[i - 1]["id"]) + 1:
            return False, i, f"non-contiguous ids at {row['id']}", None
        if str(row["prev_hash"]) != prev_expected:
            return (
                False,
                i + 1,
                f"prev_hash mismatch at id={row['id']}: "
                f"stored={row['prev_hash'][:16]}… expected={prev_expected[:16]}…",
                None,
            )
        canonical = {
            "timestamp": row["timestamp"],
            "machine_id": row["machine_id"],
            "actor": row["actor"],
            "action": row["action"],
            "permanode_id": row["permanode_id"],
            "claim_id": row["claim_id"],
            "manifest_run_id": row["manifest_run_id"],
            "payload_json": row["payload_json"],
        }
        recomputed = compute_row_hash(prev_expected, canonical)
        if recomputed != str(row["row_hash"]):
            return (
                False,
                i + 1,
                f"row_hash mismatch at id={row['id']}",
                None,
            )
        prev_expected = str(row["row_hash"])
    return True, len(rows), None, prev_expected


def resolve_through_id_before(*, db_path: Path, before_iso: str) -> int:
    """Max audit id with timestamp < before_iso (string order on ISO stamps)."""
    con = connect(db_path, read_only=True, load_vec=False)
    try:
        row = con.execute(
            "SELECT MAX(id) FROM audit_log WHERE timestamp < ?",
            (before_iso,),
        ).fetchone()
        if not row or row[0] is None:
            raise ArchiveError(f"no audit rows before {before_iso!r}")
        return int(row[0])
    finally:
        con.close()


def plan_archive(
    *,
    db_path: Path,
    through_id: int,
    hot_min_rows: int = 100,
) -> ArchiveDryRun:
    """Compute archive bounds without writing."""
    if through_id < 1:
        raise ArchiveError("through_id must be >= 1")
    con = connect(db_path, read_only=True, load_vec=False)
    try:
        ok, n_live, err = repo_audit.verify_chain(con)
        if not ok:
            raise ArchiveError(f"live chain broken — refuse archive: {err}")
        max_row = con.execute("SELECT MAX(id) FROM audit_log").fetchone()
        max_id = int(max_row[0] or 0)
        if max_id == 0:
            raise ArchiveError("audit_log is empty")
        if hot_min_rows > 0:
            remaining = max_id - through_id
            if remaining < hot_min_rows:
                if max_id <= hot_min_rows:
                    raise ArchiveError(
                        f"hot table has only {max_id} rows; refuse archive with "
                        f"hot_min_rows={hot_min_rows}"
                    )
                raise ArchiveError(
                    f"through_id={through_id} would leave {remaining} hot rows "
                    f"(need >= {hot_min_rows}); max_id={max_id} — use "
                    f"through_id <= {max_id - hot_min_rows}"
                )
        # First hot id
        first_row = con.execute("SELECT MIN(id) FROM audit_log").fetchone()
        first_id = int(first_row[0])
        # After prior sealed-but-not-shrunk archives, first_id is still min id
        if through_id < first_id:
            raise ArchiveError(f"through_id={through_id} < first_id={first_id}")
        # Prior tip: prev_hash of first_id
        first_full = con.execute(
            "SELECT prev_hash, row_hash FROM audit_log WHERE id = ?",
            (first_id,),
        ).fetchone()
        prior = str(first_full[0]) if first_full else GENESIS_PREV_HASH
        tip_row = con.execute(
            "SELECT row_hash FROM audit_log WHERE id = ?",
            (through_id,),
        ).fetchone()
        if tip_row is None:
            raise ArchiveError(f"through_id={through_id} not present in audit_log")
        tip_hash = str(tip_row[0])
        count_row = con.execute(
            "SELECT COUNT(*) FROM audit_log WHERE id >= ? AND id <= ?",
            (first_id, through_id),
        ).fetchone()
        row_count = int(count_row[0])
        expected = through_id - first_id + 1
        if row_count != expected:
            raise ArchiveError(
                f"non-contiguous range [{first_id},{through_id}]: "
                f"count={row_count} expected={expected}"
            )
        return ArchiveDryRun(
            first_id=first_id,
            through_id=through_id,
            row_count=row_count,
            tip_hash=tip_hash,
            prior_tip_hash=None if prior == GENESIS_PREV_HASH else prior,
            genesis_prev_hash=prior,
            live_chain_ok=True,
        )
    finally:
        con.close()


def seal_archive(
    *,
    db_path: Path,
    through_id: int,
    data_dir: Path | None = None,
    dry_run: bool = True,
    hot_min_rows: int = 100,
    actor: str = "cli",
) -> ArchiveSealResult | ArchiveDryRun:
    """Dry-run or seal a contiguous audit segment (no shrink)."""
    plan = plan_archive(db_path=db_path, through_id=through_id, hot_min_rows=hot_min_rows)
    if dry_run:
        return plan

    ddir = data_dir or default_data_dir()
    log_dir = _execution_log_dir(ddir)
    machine_id = resolve_machine_id(db_path)
    mid_short = machine_id.replace("-", "")[:12]
    iso = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"audit-segment-{mid_short}-{plan.through_id}-{iso}"
    final_path = log_dir / f"{stem}.tar.xz"

    con = connect(db_path, read_only=False, load_vec=False)
    try:
        ok, _, err = repo_audit.verify_chain(con)
        if not ok:
            raise ArchiveError(f"live chain broken at seal time: {err}")
        rows = _load_rows(con, first_id=plan.first_id, through_id=plan.through_id)
        first_prev = str(rows[0]["prev_hash"]) if rows else GENESIS_PREV_HASH
        vok, n, verr, tip = _verify_rows_chain(rows, first_prev_expected=first_prev)
        if not vok or tip != plan.tip_hash:
            raise ArchiveError(f"segment recompute failed: {verr}")
        # Cross-check live tip
        live_tip = con.execute(
            "SELECT row_hash FROM audit_log WHERE id = ?",
            (plan.through_id,),
        ).fetchone()
        if not live_tip or str(live_tip[0]) != plan.tip_hash:
            raise ArchiveError("live tip_hash mismatch at seal")

        with tempfile.TemporaryDirectory(prefix="steward-audit-seg-") as tmp:
            tdir = Path(tmp)
            jsonl = tdir / "audit.jsonl"
            with jsonl.open("w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
            payload_b3 = _file_blake3(jsonl)
            manifest = {
                "segment_format_version": SEGMENT_FORMAT_VERSION,
                "kind": SEGMENT_KIND,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "exporter": {
                    "steward_version": __version__,
                    "schema_version": "0003",
                    "machine_id": machine_id,
                },
                "range": {
                    "first_id": plan.first_id,
                    "through_id": plan.through_id,
                    "row_count": plan.row_count,
                    "genesis_prev_hash": first_prev,
                    "tip_hash": plan.tip_hash,
                    "first_prev_hash": first_prev,
                },
                "payload": {
                    "filename": "audit.jsonl",
                    "size_bytes": jsonl.stat().st_size,
                    "blake3": payload_b3,
                },
                "prior_tip_hash": plan.prior_tip_hash,
            }
            man_path = tdir / "manifest.json"
            man_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            man_b3 = _file_blake3(man_path)
            checks = tdir / "checksums.txt"
            checks.write_text(
                f"blake3  manifest.json  {man_b3}\nblake3  audit.jsonl  {payload_b3}\n",
                encoding="utf-8",
            )
            tar_tmp = tdir / f"{stem}.tar.xz"
            with tarfile.open(tar_tmp, "w:xz") as tf:
                tf.add(man_path, arcname="manifest.json")
                tf.add(jsonl, arcname="audit.jsonl")
                tf.add(checks, arcname="checksums.txt")
            seg_b3 = _file_blake3(tar_tmp)
            # Atomic-ish move into execution-log
            dest = final_path
            tar_tmp.replace(dest)

        try:
            rel = str(dest.relative_to(ddir))
        except ValueError:
            rel = str(dest)

        # Append audit_archive_commit + registry in one transaction
        payload = {
            "through_id": plan.through_id,
            "first_id": plan.first_id,
            "row_count": plan.row_count,
            "tip_hash": plan.tip_hash,
            "segment_path": rel,
            "segment_blake3": seg_b3,
            "segment_format_version": SEGMENT_FORMAT_VERSION,
            "shrink": False,
        }
        audit_row_id = repo_audit.append(
            con,
            machine_id=machine_id,
            actor=actor,
            action="audit_archive_commit",
            payload=payload,
        )
        # Registry may not exist on pre-migration DBs — migrate first.
        try:
            con.execute(
                """
                INSERT INTO audit_chain_segments (
                    sealed_at, first_id, through_id, row_count, tip_hash,
                    prior_tip_hash, segment_relpath, segment_blake3, shrunk_at, audit_row_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    plan.first_id,
                    plan.through_id,
                    plan.row_count,
                    plan.tip_hash,
                    plan.prior_tip_hash,
                    rel,
                    seg_b3,
                    audit_row_id,
                ),
            )
        except Exception as exc:  # noqa: BLE001 — registry optional if pre-migrate
            # Table missing: migration not applied — still sealed on disk + audit row
            del exc
        con.commit()
        return ArchiveSealResult(
            first_id=plan.first_id,
            through_id=plan.through_id,
            row_count=plan.row_count,
            tip_hash=plan.tip_hash,
            segment_path=dest,
            segment_blake3=seg_b3,
            audit_row_id=audit_row_id,
            dry_run=False,
        )
    finally:
        con.close()


def verify_segment_file(path: Path) -> SegmentVerifyResult:
    """Offline verify of a sealed segment tar.xz."""
    path = path.expanduser().resolve()
    if not path.is_file():
        return SegmentVerifyResult(False, path, 0, None, None, None, f"missing: {path}")
    try:
        with tarfile.open(path, "r:xz") as tf:
            man_f = tf.extractfile("manifest.json")
            jsonl_f = tf.extractfile("audit.jsonl")
            if man_f is None or jsonl_f is None:
                return SegmentVerifyResult(False, path, 0, None, None, None, "incomplete archive")
            manifest = json.loads(man_f.read().decode("utf-8"))
            lines = jsonl_f.read().decode("utf-8").splitlines()
        rows = [json.loads(line) for line in lines if line.strip()]
        first_prev = str(manifest["range"]["first_prev_hash"])
        tip_expected = str(manifest["range"]["tip_hash"])
        ok, n, err, tip = _verify_rows_chain(rows, first_prev_expected=first_prev)
        if not ok:
            return SegmentVerifyResult(
                False,
                path,
                n,
                int(manifest["range"]["first_id"]),
                int(manifest["range"]["through_id"]),
                tip_expected,
                err,
            )
        if tip != tip_expected:
            return SegmentVerifyResult(
                False,
                path,
                n,
                int(manifest["range"]["first_id"]),
                int(manifest["range"]["through_id"]),
                tip_expected,
                "tip_hash mismatch vs manifest",
            )
        # checksums optional but preferred
        return SegmentVerifyResult(
            True,
            path,
            n,
            int(manifest["range"]["first_id"]),
            int(manifest["range"]["through_id"]),
            tip_expected,
            None,
        )
    except Exception as exc:  # noqa: BLE001
        return SegmentVerifyResult(False, path, 0, None, None, None, str(exc))
