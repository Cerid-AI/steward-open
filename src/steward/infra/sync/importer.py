# SPDX-License-Identifier: Apache-2.0

"""Import a cross-machine inventory snapshot (ADR-0013).

Reads a ``tar.xz`` envelope produced by :func:`steward.infra.sync.exporter.export_inventory`,
verifies it against its manifest, copies the payload SQLite file into
``<data_dir>/imports/<exporter_machine_id>/<iso8601>.db``, and upserts
a row into the local ``attached_inventories`` table so future
read-side queries can ATTACH it.

The importer is structurally read-only against the local inventory's
data tables:

* The only local mutation is INSERT OR REPLACE into
  ``attached_inventories`` + the ``inventory_attached`` audit row.
* The payload .db is copied verbatim — never modified after blake3
  verification. The on-disk file is left at default mode; the
  ATTACH-side read-only enforcement comes from the ``?mode=ro`` URI
  flag at attach time.

Refusal conditions (raise :class:`ImportError`):

1. Source envelope doesn't exist.
2. Envelope is not a tar.xz file or is missing a required member
   (``manifest.json`` / ``inventory.db`` / ``checksums.txt``).
3. Wire-format version is in the future
   (`manifest.wire_format_version > WIRE_FORMAT_VERSION`).
4. Manifest blake3 doesn't match the manifest bytes.
5. Payload blake3 doesn't match the payload bytes.
6. The exporter's machine_id matches the LOCAL machine_id — operators
   cannot import their own machine's snapshot.
7. The payload's audit chain doesn't verify.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import blake3 as _blake3

from steward.infra.db import repo_audit, repo_meta
from steward.infra.db.connect import connect
from steward.infra.sync.manifest import WIRE_FORMAT_VERSION, WireManifest

logger = logging.getLogger("steward.infra.sync.importer")


class ImportError_(RuntimeError):
    """Raised when the import cannot proceed.

    Named with a trailing underscore to avoid shadowing the builtin
    :class:`ImportError`. Re-exported from
    :mod:`steward.infra.sync` as ``ImportError`` (the package alias).
    """


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Outcome of one :func:`import_inventory` call."""

    envelope_path: Path
    payload_path: Path
    machine_id: str
    exporter_hostname: str | None
    exporter_version: str
    payload_blake3: str
    audit_rows: int
    claim_rows: int
    permanode_rows: int
    duration_seconds: float
    replaced_existing: bool


def _file_blake3(path: Path, *, chunk_size: int = 1 << 20) -> str:
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


def _read_member(tar: tarfile.TarFile, name: str) -> bytes:
    """Read one member from the envelope as bytes; raise if missing."""
    try:
        member = tar.getmember(name)
    except KeyError as exc:
        raise ImportError_(f"envelope missing required member: {name}") from exc
    if not member.isfile():
        raise ImportError_(f"envelope member is not a regular file: {name}")
    fileobj = tar.extractfile(member)
    if fileobj is None:
        raise ImportError_(f"envelope member could not be read: {name}")
    return fileobj.read()


def _extract_payload(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract ``inventory.db`` from the envelope to ``dest`` (a file path)."""
    try:
        member = tar.getmember("inventory.db")
    except KeyError as exc:
        raise ImportError_("envelope missing required member: inventory.db") from exc
    fileobj = tar.extractfile(member)
    if fileobj is None:
        raise ImportError_("envelope member could not be read: inventory.db")
    with open(dest, "wb") as out:
        shutil.copyfileobj(fileobj, out, length=1 << 20)


def _local_machine_id(db_path: Path) -> str:
    con = connect(db_path, read_only=True, load_vec=False)
    try:
        value = repo_meta.get(con, "machine_id")
    finally:
        con.close()
    if not value:
        raise ImportError_(f"local inventory.db has no machine_id in meta: {db_path}. Run `steward db migrate` first.")
    return value


def _verify_payload_chain(payload_path: Path) -> int:
    """Run ``verify_chain`` against the imported payload.

    Returns rows_checked. Raises :class:`ImportError_` on chain break.
    """
    con = connect(payload_path, read_only=True, load_vec=False)
    try:
        ok, rows, err = repo_audit.verify_chain(con)
    finally:
        con.close()
    if not ok:
        raise ImportError_(f"imported audit chain failed verification: {err} (rows checked: {rows})")
    return rows


def _payload_meta(payload_path: Path) -> tuple[int, int, int]:
    """Pull (audit_rows, claim_rows, permanode_rows) from the payload."""
    con = sqlite3.connect(f"file:{payload_path}?mode=ro", uri=True)
    try:
        audit = int(con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
        claims = int(con.execute("SELECT COUNT(*) FROM claims").fetchone()[0])
        permanodes = int(con.execute("SELECT COUNT(*) FROM permanodes").fetchone()[0])
    finally:
        con.close()
    return (audit, claims, permanodes)


def _upsert_attached(
    *,
    db_path: Path,
    local_machine_id: str,
    manifest: WireManifest,
    payload_path: Path,
    audit_rows: int,
) -> bool:
    """Upsert one row in ``attached_inventories`` + append audit row.

    Returns True if an existing row was replaced, False if this is a
    fresh INSERT. Both branches append a single ``inventory_attached``
    audit row to the LOCAL chain.
    """
    con = connect(db_path, read_only=False, load_vec=False)
    try:
        existing = con.execute(
            "SELECT file_path FROM attached_inventories WHERE machine_id = ?",
            (manifest.exporter.machine_id,),
        ).fetchone()
        replaced = existing is not None

        con.execute(
            """
            INSERT OR REPLACE INTO attached_inventories (
                machine_id, file_path, imported_at, exporter_version,
                exporter_hostname, payload_blake3, audit_rows,
                chain_verified_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manifest.exporter.machine_id,
                str(payload_path),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                manifest.exporter.steward_version,
                manifest.exporter.hostname,
                manifest.payload.blake3,
                audit_rows,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                None,
            ),
        )
        repo_audit.append(
            con,
            machine_id=local_machine_id,
            actor="steward-db",
            action="inventory_attached",
            payload={
                "exporter_machine_id": manifest.exporter.machine_id,
                "exporter_hostname": manifest.exporter.hostname,
                "exporter_version": manifest.exporter.steward_version,
                "payload_path": str(payload_path),
                "payload_blake3": manifest.payload.blake3,
                "audit_rows": audit_rows,
                "claim_rows": manifest.payload.claim_rows,
                "permanode_rows": manifest.payload.permanode_rows,
                "replaced_existing": replaced,
                "previous_payload_path": str(existing[0]) if existing else None,
            },
        )
        con.commit()
    finally:
        con.close()
    return replaced


def import_inventory(
    *,
    envelope_path: Path,
    db_path: Path,
    imports_dir: Path,
) -> ImportResult:
    """Import the envelope at ``envelope_path`` into the local instance.

    Parameters
    ----------
    envelope_path:
        Existing tar.xz envelope produced by ``steward db export``.
    db_path:
        The local ``inventory.db`` — used to read the local
        ``meta.machine_id`` (for the same-machine refusal check) and
        to upsert the ``attached_inventories`` row.
    imports_dir:
        Directory under which payload .db files are stored. The
        importer creates
        ``<imports_dir>/<exporter_machine_id>/<iso8601>.db`` under it.

    The payload .db is left writable on disk (POSIX permissions
    untouched). Read-only enforcement is process-level: callers MUST
    ATTACH it with the ``?mode=ro`` URI flag.
    """
    if not envelope_path.exists():
        raise ImportError_(f"envelope not found: {envelope_path}")
    if not db_path.exists():
        raise ImportError_(f"local inventory.db not found: {db_path}. Run `steward db migrate` first.")

    started = time.monotonic()
    local_id = _local_machine_id(db_path)

    # ── 1. Open envelope; read manifest + checksums + payload bytes for verification.
    try:
        tar = tarfile.open(envelope_path, "r:xz")
    except tarfile.ReadError as exc:
        raise ImportError_(f"envelope is not a readable tar.xz: {envelope_path}") from exc

    try:
        manifest_bytes = _read_member(tar, "manifest.json")
        _checksums_bytes = _read_member(tar, "checksums.txt")  # presence-check
    finally:
        # We still need the tar to extract the payload — but we close
        # and re-open to avoid holding the handle while we run blake3
        # over a potentially large payload from disk.
        tar.close()

    try:
        manifest = WireManifest.model_validate_json(manifest_bytes.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — manifest parse failure is a refusal
        raise ImportError_(f"manifest.json failed to parse: {exc}") from exc

    # ── 2. Wire-format version gate.
    if manifest.wire_format_version > WIRE_FORMAT_VERSION:
        raise ImportError_(
            f"envelope wire_format_version={manifest.wire_format_version} "
            f"is newer than this Steward (supports {WIRE_FORMAT_VERSION}). "
            f"Upgrade Steward to import this envelope."
        )

    # ── 3. Same-machine refusal.
    if manifest.exporter.machine_id == local_id:
        raise ImportError_(
            f"envelope was exported from this same machine "
            f"(machine_id={local_id}). "
            f"Cross-machine import requires a different exporter."
        )

    # ── 4. Verify the manifest bytes match the payload reference.
    #
    # We don't re-hash manifest_bytes against checksums.txt — checksums.txt
    # is purely a convenience for out-of-band integrity checks. The
    # authoritative payload-side check is `manifest.payload.blake3`.

    # ── 5. Stage the payload to a temp dir, blake3-verify, then move into
    # the imports directory at its final location.
    target_subdir = imports_dir / manifest.exporter.machine_id
    target_subdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_path = target_subdir / f"{stamp}.db"

    with tempfile.TemporaryDirectory(prefix="steward-import-", dir=target_subdir) as td:
        scratch = Path(td)
        staged = scratch / "payload.db"
        with tarfile.open(envelope_path, "r:xz") as tar:
            _extract_payload(tar, staged)

        actual_blake3 = _file_blake3(staged)
        if actual_blake3 != manifest.payload.blake3:
            raise ImportError_(
                f"payload blake3 mismatch: manifest says "
                f"{manifest.payload.blake3} but extracted file is "
                f"{actual_blake3}"
            )

        # Manifest hash too — over the bytes we just parsed.
        manifest_actual_blake3 = _bytes_blake3(manifest_bytes)
        # checksums.txt has them in payload-then-manifest order.
        checksum_lines = [ln.strip() for ln in _checksums_bytes.decode("utf-8").splitlines() if ln.strip()]
        manifest_line = next(
            (ln for ln in checksum_lines if ln.endswith("manifest.json")),
            None,
        )
        if manifest_line is None:
            raise ImportError_("checksums.txt missing manifest.json entry")
        recorded_manifest_hash = manifest_line.split()[0]
        if recorded_manifest_hash != manifest_actual_blake3:
            raise ImportError_(
                f"manifest.json blake3 mismatch: checksums.txt says "
                f"{recorded_manifest_hash} but actual is "
                f"{manifest_actual_blake3}"
            )

        # ── 6. Verify the payload's audit chain.
        audit_rows = _verify_payload_chain(staged)
        if audit_rows != manifest.payload.audit_rows:
            raise ImportError_(
                f"audit row count mismatch: manifest says {manifest.payload.audit_rows} but payload has {audit_rows}"
            )

        # ── 7. Atomic move into final location.
        staged.replace(final_path)

    # ── 8. Pull row counts straight from the moved payload (defensive).
    payload_audit, payload_claims, payload_pn = _payload_meta(final_path)

    # ── 9. Upsert attached_inventories + append local audit row.
    replaced = _upsert_attached(
        db_path=db_path,
        local_machine_id=local_id,
        manifest=manifest,
        payload_path=final_path,
        audit_rows=payload_audit,
    )

    duration = time.monotonic() - started
    return ImportResult(
        envelope_path=envelope_path,
        payload_path=final_path,
        machine_id=manifest.exporter.machine_id,
        exporter_hostname=manifest.exporter.hostname,
        exporter_version=manifest.exporter.steward_version,
        payload_blake3=manifest.payload.blake3,
        audit_rows=payload_audit,
        claim_rows=payload_claims,
        permanode_rows=payload_pn,
        duration_seconds=duration,
        replaced_existing=replaced,
    )


__all__ = ["ImportError_", "ImportResult", "import_inventory"]
