# SPDX-License-Identifier: Apache-2.0

"""Export the local ``inventory.db`` as a portable cross-machine snapshot.

The output is a ``tar.xz`` envelope containing three files:

* ``inventory.db`` — a SQLite payload (the same schema as the local
  DB, with excluded tables emptied + VACUUMed).
* ``manifest.json`` — :class:`steward.infra.sync.manifest.WireManifest`
  describing the producer and the payload.
* ``checksums.txt`` — blake3 of the two above.

The exporter:

1. Calls :func:`steward.infra.db.backup.backup_inventory_db` to write
   a hot-backup snapshot. The audit chain is preserved verbatim;
   ``backup_inventory_db`` adds a ``db_backup_created`` row to the
   live DB *after* the snapshot is sealed, so the snapshot doesn't
   self-reference.
2. Opens the snapshot writeable and ``DELETE``\\s from the excluded
   tables (``tiers``, ``embeddings``, ``embeddings_vec``,
   ``legacy_import_log``, ``attached_inventories``), then ``VACUUM``\\s.
3. Computes the blake3 of the cleaned snapshot.
4. Builds the manifest + checksums file.
5. Tars the three files into the destination ``tar.xz``.
6. Appends a ``db_export_created`` audit row to the LOCAL DB
   (not the snapshot — its chain is sealed) recording the export.
"""
from __future__ import annotations

import logging
import socket
import sqlite3
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import blake3 as _blake3

from steward._version import __version__ as STEWARD_VERSION
from steward.infra.db import repo_audit, repo_meta
from steward.infra.db.backup import backup_inventory_db
from steward.infra.db.connect import connect
from steward.infra.observability import log_swallowed_error
from steward.infra.sync.manifest import (
    EXCLUDED_TABLES_DEFAULT,
    EXCLUDED_TABLES_WITH_EMBEDDINGS,
    EnvelopeChecksums,
    ExporterMetadata,
    PayloadMetadata,
    WireManifest,
)

logger = logging.getLogger("steward.infra.sync.exporter")


class ExportError(RuntimeError):
    """Raised when the export cannot proceed (target exists, source missing, etc.)."""


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Outcome of one :func:`export_inventory` call."""

    source_db_path: Path
    envelope_path: Path
    envelope_size_bytes: int
    payload_size_bytes: int
    payload_blake3: str
    duration_seconds: float
    audit_rows: int
    claim_rows: int
    permanode_rows: int
    with_embeddings: bool


def _file_blake3(path: Path, *, chunk_size: int = 1 << 20) -> str:
    """Stream a file once and return its blake3 hex digest."""
    h = _blake3.blake3()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _bytes_blake3(data: bytes) -> str:
    return _blake3.blake3(data).hexdigest()


def _strip_excluded_tables(snapshot_path: Path, excluded: tuple[str, ...]) -> None:
    """Open the snapshot writeable, empty excluded tables, VACUUM.

    The connection loads sqlite-vec because the ``embeddings_vec``
    virtual table belongs to the ``vec0`` module and can't be touched
    (even ``DELETE``\\d) without the extension. The on-disk file
    itself doesn't depend on the extension — sqlite-vec is only
    needed at the moment we mutate ``embeddings_vec``.
    """
    con = connect(snapshot_path, read_only=False, load_vec=True)
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        # Belt-and-suspenders: only honor table names that came from our
        # own EXCLUDED_TABLES_* tuples. This guards against a future
        # caller pulling table names from anywhere user-influenced.
        allowed = set(EXCLUDED_TABLES_DEFAULT) | set(EXCLUDED_TABLES_WITH_EMBEDDINGS)
        for table in excluded:
            if table not in allowed:
                raise ExportError(f"refusing to strip unknown table: {table!r}")
            # Only DELETE if the table actually exists.
            row = con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
                (table,),
            ).fetchone()
            if row is None:
                continue
            con.execute(f"DELETE FROM {table}")  # nosec B608 — table is a member of our static allowlist
        con.commit()
        # VACUUM rebuilds the file without the deleted rows — reduces size
        # and removes any pages the deleted-but-not-overwritten data was
        # holding. VACUUM cannot run inside a transaction.
        con.isolation_level = None
        con.execute("VACUUM")
    finally:
        con.close()


def _payload_row_counts(snapshot_path: Path) -> tuple[int, int, int]:
    """Return (audit_rows, claim_rows, permanode_rows) for the snapshot."""
    con = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
    try:
        audit = int(con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
        claims = int(con.execute("SELECT COUNT(*) FROM claims").fetchone()[0])
        permanodes = int(con.execute("SELECT COUNT(*) FROM permanodes").fetchone()[0])
    finally:
        con.close()
    return (audit, claims, permanodes)


def _schema_version(db_path: Path) -> str:
    """Read meta.schema_version from the live DB."""
    con = connect(db_path, read_only=True, load_vec=False)
    try:
        value = repo_meta.get(con, "schema_version")
    finally:
        con.close()
    return value or "unknown"


def _hostname() -> str | None:
    try:
        return socket.gethostname() or None
    except OSError as exc:  # noqa: BLE001 — DNS hiccups shouldn't kill an export
        log_swallowed_error("infra.sync.exporter.hostname", exc)
        return None


def export_inventory(
    *,
    db_path: Path,
    target_path: Path,
    machine_id: str,
    with_embeddings: bool = False,
    overwrite: bool = False,
) -> ExportResult:
    """Export ``db_path`` to ``target_path`` as a tar.xz envelope.

    Parameters
    ----------
    db_path:
        Live local ``inventory.db``.
    target_path:
        Destination envelope. Must end with ``.tar.xz`` for clarity;
        not enforced (operator may pick any name). Refuses to overwrite
        an existing file unless ``overwrite=True``.
    machine_id:
        The local machine's UUID — recorded in the manifest as the
        exporter's identity and used for the local-side audit row.
    with_embeddings:
        When True, keep the ``embeddings`` + ``embeddings_vec`` tables
        in the payload. Default False — these are large and
        model-version coupled.
    overwrite:
        When True, an existing ``target_path`` is unlinked first.
    """
    if not db_path.exists():
        raise ExportError(f"source inventory.db not found: {db_path}")
    if target_path.exists() and not overwrite:
        raise ExportError(
            f"target already exists: {target_path}. Pass overwrite=True to replace it."
        )
    if not target_path.parent.exists():
        raise ExportError(
            f"target's parent directory does not exist: {target_path.parent}"
        )

    started = time.monotonic()
    excluded = (
        EXCLUDED_TABLES_WITH_EMBEDDINGS if with_embeddings else EXCLUDED_TABLES_DEFAULT
    )
    schema_version = _schema_version(db_path)

    # Step 1: hot-backup snapshot via SQLite's online-backup API.
    # We backup to a temp file inside the same parent as the target so
    # the rename at the end is atomic on most filesystems.
    target_parent = target_path.parent
    with tempfile.TemporaryDirectory(prefix="steward-export-", dir=target_parent) as td:
        scratch = Path(td)
        snapshot_path = scratch / "inventory.db"

        backup_inventory_db(
            source_path=db_path,
            target_path=snapshot_path,
            machine_id=machine_id,
            overwrite=False,
        )

        # Step 2: strip excluded tables + VACUUM.
        _strip_excluded_tables(snapshot_path, excluded)

        # Step 3: hash + count.
        audit_rows, claim_rows, permanode_rows = _payload_row_counts(snapshot_path)
        payload_blake3 = _file_blake3(snapshot_path)
        payload_size = snapshot_path.stat().st_size

        # Step 4: build manifest.
        manifest = WireManifest(
            exported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            exporter=ExporterMetadata(
                steward_version=STEWARD_VERSION,
                schema_version=schema_version,
                machine_id=machine_id,
                hostname=_hostname(),
            ),
            payload=PayloadMetadata(
                size_bytes=payload_size,
                blake3=payload_blake3,
                audit_rows=audit_rows,
                claim_rows=claim_rows,
                permanode_rows=permanode_rows,
            ),
            excluded_tables=list(excluded),
        )
        manifest_json = manifest.to_json().encode("utf-8")
        manifest_blake3 = _bytes_blake3(manifest_json)
        checksums = EnvelopeChecksums(
            manifest_blake3=manifest_blake3,
            payload_blake3=payload_blake3,
        )
        checksums_text = checksums.to_text().encode("utf-8")

        # Step 5: package envelope. Write to a sibling temp path then rename.
        scratch_envelope = scratch / "envelope.tar.xz"
        with tarfile.open(scratch_envelope, "w:xz") as tar:
            tar.add(snapshot_path, arcname="inventory.db")
            manifest_info = tarfile.TarInfo(name="manifest.json")
            manifest_info.size = len(manifest_json)
            tar.addfile(manifest_info, BytesIO(manifest_json))
            checksums_info = tarfile.TarInfo(name="checksums.txt")
            checksums_info.size = len(checksums_text)
            tar.addfile(checksums_info, BytesIO(checksums_text))

        if target_path.exists() and overwrite:
            target_path.unlink()
        scratch_envelope.replace(target_path)

    envelope_size = target_path.stat().st_size
    duration = time.monotonic() - started

    # Step 6: append db_export_created audit row to the LIVE DB.
    con = connect(db_path, read_only=False, load_vec=False)
    try:
        repo_audit.append(
            con,
            machine_id=machine_id,
            actor="steward-db",
            action="db_export_created",
            payload={
                "target": str(target_path),
                "envelope_bytes": envelope_size,
                "payload_bytes": payload_size,
                "payload_blake3": payload_blake3,
                "with_embeddings": with_embeddings,
                "audit_rows": audit_rows,
                "claim_rows": claim_rows,
                "permanode_rows": permanode_rows,
                "duration_seconds": round(duration, 3),
            },
        )
        con.commit()
    finally:
        con.close()

    return ExportResult(
        source_db_path=db_path,
        envelope_path=target_path,
        envelope_size_bytes=envelope_size,
        payload_size_bytes=payload_size,
        payload_blake3=payload_blake3,
        duration_seconds=duration,
        audit_rows=audit_rows,
        claim_rows=claim_rows,
        permanode_rows=permanode_rows,
        with_embeddings=with_embeddings,
    )


__all__ = ["ExportError", "ExportResult", "export_inventory"]
