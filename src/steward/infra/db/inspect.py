# SPDX-License-Identifier: Apache-2.0

"""Inspect facade — fetch a permanode + claims + recent audit by hash or path."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from steward.infra.db.connect import connect
from steward.infra.db.settings import inventory_db_path


@dataclass
class InspectResult:
    permanode_id: str
    canonical_hash: str
    canonical_hash_algo: str
    size_bytes: int
    first_seen_at: str
    last_seen_at: str
    claims: list[dict[str, Any]] = field(default_factory=list)
    audit_rows: list[dict[str, Any]] = field(default_factory=list)
    source: str = "local"
    """Where the permanode was found: ``"local"`` for the host's own
    inventory, ``"attached"`` when it took an attached-schema fan-out
    to resolve. Only meaningful when ``inspect(..., include_imports=True)``
    is used."""
    resolution_schema: str = "main"
    """SQLite schema alias used for resolution: ``"main"`` for local or
    ``"m_<short>"`` for an attached inventory."""


def inspect(
    target: str,
    *,
    audit_limit: int = 20,
    include_imports: bool = False,
) -> InspectResult | None:
    """Resolve ``target`` to a permanode and return its full view.

    ``target`` may be:

    * a canonical hash (xxh3-128 32-hex, blake3 64-hex, or sha256 64-hex)
    * a permanode id (32-hex)
    * a file path that appears in some claim

    With ``include_imports=True`` (v0.3.6 / ADR-0013) the lookup
    fans out to attached inventories — both for permanode resolution
    (so a hash that only exists on a foreign machine resolves too)
    and for the claims/audit listing (every claim from every
    attached schema, tagged with its source).

    Returns ``None`` if no permanode matches.
    """
    db = inventory_db_path()
    if not db.exists():
        return None

    if not include_imports:
        # Fast path: single-schema lookup (preserves v0.2.12 surface).
        con = connect(db, read_only=True, load_vec=False)
        try:
            return _inspect_single_schema(con, target, audit_limit=audit_limit)
        finally:
            con.close()

    # Fan-out path: try local first, then each attached schema.
    from steward.infra.sync.attach import attach_imports

    with attach_imports(db_path=db) as ctx:
        # Resolution: try main first, then each attached.
        permanode_row = _resolve_permanode(ctx.connection, target)
        source = "local"
        resolution_schema = ""
        if permanode_row is None:
            for schema in ctx.attached:
                permanode_row = _resolve_permanode_in_schema(ctx.connection, target, schema.alias)
                if permanode_row is not None:
                    source = "attached"
                    resolution_schema = f"{schema.alias}."
                    break
        if permanode_row is None:
            return None

        (pid, canonical_hash, algo, size_bytes, first_seen, last_seen) = permanode_row

        # Claims fan-out: collect from local + every attached schema.
        all_claims: list[dict[str, Any]] = []
        for schema_label, prefix in [("local", "")] + [("attached", f"{s.alias}.") for s in ctx.attached]:
            claims_sql = (
                f"SELECT id, machine_id, file_path, tier, volume, domain, "
                f"classification, size_bytes, observed_at, is_current "
                f"FROM {prefix}claims "  # nosec B608 — prefix from controlled allowlist
                f"WHERE permanode_id = ? "
                f"ORDER BY is_current DESC, observed_at DESC, id DESC"
            )
            try:
                cur = ctx.connection.execute(claims_sql, (pid,))
            except Exception:  # noqa: BLE001 — attached schema may not have this permanode
                continue
            cols = [d[0] for d in (cur.description or [])]
            for r in cur.fetchall():
                row = dict(zip(cols, r, strict=True))
                row["source"] = schema_label
                all_claims.append(row)

        # Audit fan-out: same pattern, but limit per-schema to keep
        # the noise bounded; the operator sees the most-recent across
        # all schemas merged.
        audit_rows: list[dict[str, Any]] = []
        for schema_label, prefix in [("local", "")] + [("attached", f"{s.alias}.") for s in ctx.attached]:
            audit_sql = (
                f"SELECT id, timestamp, action, actor, payload_json "
                f"FROM {prefix}audit_log "  # nosec B608 — prefix from controlled allowlist
                f"WHERE permanode_id = ? "
                f"ORDER BY id DESC LIMIT ?"
            )
            try:
                cur = ctx.connection.execute(audit_sql, (pid, audit_limit))
            except Exception:  # noqa: BLE001
                continue
            cols = [d[0] for d in (cur.description or [])]
            for r in cur.fetchall():
                row = dict(zip(cols, r, strict=True))
                row["source"] = schema_label
                audit_rows.append(row)
        # Sort merged audit by timestamp desc; keep top N.
        audit_rows.sort(key=lambda r: str(r.get("timestamp", "")), reverse=True)
        audit_rows = audit_rows[:audit_limit]

        return InspectResult(
            permanode_id=pid,
            canonical_hash=canonical_hash,
            canonical_hash_algo=algo,
            size_bytes=size_bytes,
            first_seen_at=first_seen,
            last_seen_at=last_seen,
            claims=all_claims,
            audit_rows=audit_rows,
            source=source,
            resolution_schema=resolution_schema or "main",
        )


def _inspect_single_schema(con: Any, target: str, *, audit_limit: int) -> InspectResult | None:
    permanode_row = _resolve_permanode(con, target)
    if permanode_row is None:
        return None
    (pid, canonical_hash, algo, size_bytes, first_seen, last_seen) = permanode_row

    claims_cur = con.execute(
        """
        SELECT id, machine_id, file_path, tier, volume, domain, classification,
               size_bytes, observed_at, is_current
        FROM claims WHERE permanode_id = ?
        ORDER BY is_current DESC, observed_at DESC, id DESC
        """,
        (pid,),
    )
    claim_cols = [d[0] for d in (claims_cur.description or [])]
    claims = [dict(zip(claim_cols, r, strict=True)) for r in claims_cur.fetchall()]

    audit_cur = con.execute(
        """
        SELECT id, timestamp, action, actor, payload_json
        FROM audit_log WHERE permanode_id = ?
        ORDER BY id DESC LIMIT ?
        """,
        (pid, audit_limit),
    )
    audit_cols = [d[0] for d in (audit_cur.description or [])]
    audit_rows = [dict(zip(audit_cols, r, strict=True)) for r in audit_cur.fetchall()]

    return InspectResult(
        permanode_id=pid,
        canonical_hash=canonical_hash,
        canonical_hash_algo=algo,
        size_bytes=size_bytes,
        first_seen_at=first_seen,
        last_seen_at=last_seen,
        claims=claims,
        audit_rows=audit_rows,
    )


def _resolve_permanode_in_schema(con: Any, target: str, alias: str) -> tuple[str, str, str, int, str, str] | None:
    """Same shape as :func:`_resolve_permanode` but against an attached schema."""
    target_lower = target.lower()
    # alias is from controlled allowlist (attached schema), not user SQL.
    for sql, param in [
        (
            f"SELECT id, canonical_hash, canonical_hash_algo, size_bytes, first_seen_at, last_seen_at FROM {alias}.permanodes WHERE id = ?",  # nosec B608
            target,
        ),
        (
            f"SELECT id, canonical_hash, canonical_hash_algo, size_bytes, first_seen_at, last_seen_at FROM {alias}.permanodes WHERE canonical_hash = ?",  # nosec B608
            target_lower,
        ),
        (
            f"SELECT p.id, p.canonical_hash, p.canonical_hash_algo, p.size_bytes, p.first_seen_at, p.last_seen_at FROM {alias}.hashes h JOIN {alias}.permanodes p ON p.id = h.permanode_id WHERE h.hex = ? LIMIT 1",  # nosec B608
            target_lower,
        ),
        (
            f"SELECT p.id, p.canonical_hash, p.canonical_hash_algo, p.size_bytes, p.first_seen_at, p.last_seen_at FROM {alias}.claims c JOIN {alias}.permanodes p ON p.id = c.permanode_id WHERE c.file_path = ? ORDER BY c.is_current DESC, c.id DESC LIMIT 1",  # nosec B608
            target,
        ),
    ]:
        try:
            row = con.execute(sql, (param,)).fetchone()
        except Exception:  # noqa: BLE001
            continue
        if row:
            return (str(row[0]), str(row[1]), str(row[2]), int(row[3]), str(row[4]), str(row[5]))
    return None


def _resolve_permanode(con: Any, target: str) -> tuple[str, str, str, int, str, str] | None:
    """Return ``(id, canonical_hash, algo, size_bytes, first_seen, last_seen)``
    or ``None`` if no match."""
    # 1. Try as a permanode id (32-hex).
    row = con.execute(
        """
        SELECT id, canonical_hash, canonical_hash_algo, size_bytes,
               first_seen_at, last_seen_at
        FROM permanodes WHERE id = ?
        """,
        (target,),
    ).fetchone()
    if row:
        return (str(row[0]), str(row[1]), str(row[2]), int(row[3]), str(row[4]), str(row[5]))

    # 2. Try as a canonical hash.
    row = con.execute(
        """
        SELECT id, canonical_hash, canonical_hash_algo, size_bytes,
               first_seen_at, last_seen_at
        FROM permanodes WHERE canonical_hash = ?
        """,
        (target.lower(),),
    ).fetchone()
    if row:
        return (str(row[0]), str(row[1]), str(row[2]), int(row[3]), str(row[4]), str(row[5]))

    # 3. Try as a hash on the hashes table (xxh3 or blake3 lookup).
    row = con.execute(
        """
        SELECT p.id, p.canonical_hash, p.canonical_hash_algo, p.size_bytes,
               p.first_seen_at, p.last_seen_at
        FROM hashes h JOIN permanodes p ON p.id = h.permanode_id
        WHERE h.hex = ?
        LIMIT 1
        """,
        (target.lower(),),
    ).fetchone()
    if row:
        return (str(row[0]), str(row[1]), str(row[2]), int(row[3]), str(row[4]), str(row[5]))

    # 4. Try as a file path on any claim.
    row = con.execute(
        """
        SELECT p.id, p.canonical_hash, p.canonical_hash_algo, p.size_bytes,
               p.first_seen_at, p.last_seen_at
        FROM claims c JOIN permanodes p ON p.id = c.permanode_id
        WHERE c.file_path = ?
        ORDER BY c.is_current DESC, c.id DESC
        LIMIT 1
        """,
        (target,),
    ).fetchone()
    if row:
        return (str(row[0]), str(row[1]), str(row[2]), int(row[3]), str(row[4]), str(row[5]))

    return None
