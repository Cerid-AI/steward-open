# SPDX-License-Identifier: Apache-2.0

"""DB admin facade — migrate, verify, integrity.

The CLI calls these helpers rather than importing :mod:`steward.infra.db.connect`
directly (import-linter contract). They orchestrate alembic + audit-chain
verification + sqlite integrity checks behind one stable surface.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config

from steward.infra.db import repo_audit, repo_meta
from steward.infra.db.connect import connect, vec_version
from steward.infra.db.settings import inventory_db_path

logger = logging.getLogger("steward.infra.db.admin")

ALEMBIC_INI = Path(__file__).resolve().parents[3].parent / "alembic.ini"
# parents[3] = src/steward/infra/db → src/steward/infra → src/steward → src.
# parent = repo root (the package lives at src/steward, alembic.ini is at repo root).


@dataclass(frozen=True)
class MigrateResult:
    db_path: Path
    schema_version: str | None
    machine_id: str
    vec_version: str | None


@dataclass(frozen=True)
class VerifyResult:
    rows_checked: int
    ok: bool
    error: str | None


def _alembic_config(db_path: Path) -> Config:
    """Build an alembic ``Config`` pointed at the script_location, and
    propagate the migration target to the env via ``STEWARD_DB_PATH``."""
    if not ALEMBIC_INI.exists():
        raise FileNotFoundError(f"alembic.ini not found at {ALEMBIC_INI}")
    cfg = Config(str(ALEMBIC_INI))
    # env.py picks the path up from this var. We always set it (not just
    # "if absent") because the admin facade may have already resolved a
    # caller-supplied override and is now passing it through.
    os.environ["STEWARD_DB_PATH"] = str(db_path)
    return cfg


def _ensure_machine_id(con: sqlite3.Connection) -> str:
    """Read meta.machine_id, populating it with a fresh uuid4 if absent."""
    existing = repo_meta.get(con, "machine_id")
    if existing:
        return existing
    new_id = str(uuid.uuid4())
    repo_meta.set_(con, "machine_id", new_id)
    return new_id


def migrate(db_path: Path | None = None) -> MigrateResult:
    """Run alembic upgrade head, then post-migrate housekeeping.

    Idempotent: running twice is a no-op past the first revision.
    """
    target = (db_path or inventory_db_path()).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)

    cfg = _alembic_config(target)
    alembic_command.upgrade(cfg, "head")

    # Post-migrate housekeeping: machine_id + log the vec extension version.
    con = connect(target)
    try:
        machine_id = _ensure_machine_id(con)
        repo_meta.set_(con, "steward_version", _runtime_steward_version())
        ver = vec_version(con)
        if ver is not None:
            repo_meta.set_(con, "sqlite_vec_version", ver)
        con.commit()
        schema_version = repo_meta.get(con, "schema_version")
    finally:
        con.close()

    return MigrateResult(
        db_path=target,
        schema_version=schema_version,
        machine_id=machine_id,
        vec_version=ver,
    )


def verify_chain(db_path: Path | None = None) -> VerifyResult:
    """Walk audit_log and verify the hash chain.

    Returns a value object rather than raising; the CLI translates that
    into an exit code + friendly message.
    """
    target = (db_path or inventory_db_path()).expanduser()
    if not target.exists():
        return VerifyResult(rows_checked=0, ok=False,
                            error=f"inventory.db not found at {target}")
    con = connect(target, read_only=True, load_vec=False)
    try:
        ok, n, err = repo_audit.verify_chain(con)
    finally:
        con.close()
    return VerifyResult(rows_checked=n, ok=ok, error=err)


def resolve_machine_id(db_path: Path | None = None) -> str:
    """Return the machine_id from meta, running migrate to seed it if needed.

    Operator-facing facade so the CLI doesn't need to know about
    ``infra.db.connect`` or ``repo_meta`` directly (import-linter contract).
    """
    target = (db_path or inventory_db_path()).expanduser()
    if not target.exists():
        return migrate(target).machine_id
    con = connect(target, read_only=True, load_vec=False)
    try:
        mid = repo_meta.get(con, "machine_id")
    finally:
        con.close()
    if not mid:
        return migrate(target).machine_id
    return mid


def integrity_check(db_path: Path | None = None) -> tuple[bool, str]:
    """Run SQLite's built-in ``PRAGMA integrity_check``.

    Returns ``(ok, message)``. SQLite returns ``'ok'`` on success or
    a multi-line error report on failure.
    """
    target = (db_path or inventory_db_path()).expanduser()
    if not target.exists():
        return (False, f"inventory.db not found at {target}")
    con = connect(target, read_only=True, load_vec=False)
    try:
        row = con.execute("PRAGMA integrity_check").fetchone()
    finally:
        con.close()
    if not row:
        return (False, "PRAGMA integrity_check returned no rows")
    text = str(row[0])
    return (text == "ok", text)


def _runtime_steward_version() -> str:
    """Read the package version without importing it eagerly.

    Lazy so a partial install (e.g. before the package is built) doesn't
    crash the migration. ``log_swallowed_error`` records the swallow so a
    missing version isn't invisible.
    """
    try:
        from steward._version import __version__

        return __version__
    except Exception as exc:  # noqa: BLE001 — migration must not fail on version lookup
        from steward.infra.observability import log_swallowed_error

        log_swallowed_error("infra.db.admin.runtime_version", exc)
        return "unknown"
