"""initial schema — permanodes, claims, hashes, tiers, embeddings, scan_runs, audit_log

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# All DDL is hand-written SQLite. STRICT tables enforce per-column types
# at INSERT time. WHERE-clause partial indexes keep the is_current=1 hot
# path tight. Triggers on audit_log make the table append-only at the
# SQLite engine level — not at the application layer.


_DDL_STATEMENTS = [
    # ── permanodes ──────────────────────────────────────────────────────────
    """
    CREATE TABLE permanodes (
        id                  TEXT PRIMARY KEY,
        canonical_hash      TEXT NOT NULL,
        canonical_hash_algo TEXT NOT NULL DEFAULT 'blake3',
        size_bytes          INTEGER NOT NULL,
        first_seen_at       TEXT NOT NULL,
        last_seen_at        TEXT NOT NULL,
        UNIQUE (canonical_hash, size_bytes)
    ) STRICT
    """,
    "CREATE INDEX ix_permanodes_canonical_hash ON permanodes(canonical_hash)",
    "CREATE INDEX ix_permanodes_size ON permanodes(size_bytes)",
    # ── scan_runs ───────────────────────────────────────────────────────────
    # Forward-referenced by claims(scan_run_id); create before claims so the FK
    # parses cleanly under PRAGMA foreign_keys=ON.
    """
    CREATE TABLE scan_runs (
        id                  INTEGER PRIMARY KEY,
        started_at          TEXT NOT NULL,
        finished_at         TEXT,
        machine_id          TEXT NOT NULL,
        root_path           TEXT NOT NULL,
        workers             INTEGER NOT NULL,
        include_containers  INTEGER NOT NULL,
        files_walked        INTEGER NOT NULL DEFAULT 0,
        files_hashed        INTEGER NOT NULL DEFAULT 0,
        files_skipped       INTEGER NOT NULL DEFAULT 0,
        bytes_hashed        INTEGER NOT NULL DEFAULT 0,
        errors              INTEGER NOT NULL DEFAULT 0,
        resumed_from        INTEGER REFERENCES scan_runs(id),
        notes               TEXT
    ) STRICT
    """,
    # ── claims ──────────────────────────────────────────────────────────────
    """
    CREATE TABLE claims (
        id                  INTEGER PRIMARY KEY,
        permanode_id        TEXT NOT NULL REFERENCES permanodes(id) ON DELETE CASCADE,
        machine_id          TEXT NOT NULL,
        file_path           TEXT NOT NULL,
        parent_dir          TEXT NOT NULL,
        basename            TEXT NOT NULL,
        extension           TEXT,
        tier                TEXT NOT NULL,
        volume              TEXT NOT NULL,
        domain              TEXT,
        classification      TEXT,
        container_path      TEXT,
        container_sha256    TEXT,
        size_bytes          INTEGER NOT NULL,
        mtime_iso           TEXT,
        observed_at         TEXT NOT NULL,
        scan_run_id         INTEGER NOT NULL REFERENCES scan_runs(id),
        is_current          INTEGER NOT NULL DEFAULT 1,
        legacy_sha256       TEXT,
        UNIQUE (machine_id, file_path, container_path, scan_run_id)
    ) STRICT
    """,
    "CREATE INDEX ix_claims_permanode ON claims(permanode_id)",
    "CREATE INDEX ix_claims_tier ON claims(tier)",
    "CREATE INDEX ix_claims_volume ON claims(volume)",
    "CREATE INDEX ix_claims_domain ON claims(domain)",
    "CREATE INDEX ix_claims_classification ON claims(classification)",
    "CREATE INDEX ix_claims_path_prefix ON claims(file_path)",
    "CREATE INDEX ix_claims_parent_dir ON claims(parent_dir)",
    "CREATE INDEX ix_claims_is_current ON claims(is_current) WHERE is_current = 1",
    "CREATE INDEX ix_claims_legacy_sha256 ON claims(legacy_sha256)",
    # ── hashes ──────────────────────────────────────────────────────────────
    """
    CREATE TABLE hashes (
        id                  INTEGER PRIMARY KEY,
        permanode_id        TEXT NOT NULL REFERENCES permanodes(id) ON DELETE CASCADE,
        algo                TEXT NOT NULL,
        hex                 TEXT NOT NULL,
        computed_at         TEXT NOT NULL,
        UNIQUE (permanode_id, algo)
    ) STRICT
    """,
    "CREATE INDEX ix_hashes_algo_hex ON hashes(algo, hex)",
    # ── tiers ───────────────────────────────────────────────────────────────
    """
    CREATE TABLE tiers (
        name                TEXT PRIMARY KEY,
        priority            INTEGER NOT NULL,
        is_writable         INTEGER NOT NULL,
        stash_root          TEXT,
        path_prefixes       TEXT NOT NULL
    ) STRICT
    """,
    # ── embeddings ──────────────────────────────────────────────────────────
    # Column shape is locked here so v0.2 onnxruntime work can drop in
    # without ALTERing the table (ADR-0008 / risk row).
    """
    CREATE TABLE embeddings (
        id                  INTEGER PRIMARY KEY,
        permanode_id        TEXT NOT NULL REFERENCES permanodes(id) ON DELETE CASCADE,
        model_name          TEXT NOT NULL,
        model_version       TEXT NOT NULL,
        dimension           INTEGER NOT NULL,
        computed_at         TEXT NOT NULL,
        UNIQUE (permanode_id, model_name, model_version)
    ) STRICT
    """,
    "CREATE INDEX ix_embeddings_model ON embeddings(model_name, model_version)",
    # sqlite-vec virtual table — keyed by INTEGER PRIMARY KEY (embedding_id)
    # which references embeddings.id. Single dimension for v0.1 (e5-small =
    # 384). v0.2 can drop+recreate with the new dimension and a model_name
    # override on the embeddings row.
    """
    CREATE VIRTUAL TABLE embeddings_vec USING vec0(
        embedding_id INTEGER PRIMARY KEY,
        embedding float[384]
    )
    """,
    # ── audit_log ───────────────────────────────────────────────────────────
    """
    CREATE TABLE audit_log (
        id                  INTEGER PRIMARY KEY,
        timestamp           TEXT NOT NULL,
        machine_id          TEXT NOT NULL,
        actor               TEXT NOT NULL,
        action              TEXT NOT NULL,
        permanode_id        TEXT REFERENCES permanodes(id),
        claim_id            INTEGER REFERENCES claims(id),
        manifest_run_id     TEXT,
        payload_json        TEXT NOT NULL,
        prev_hash           TEXT NOT NULL,
        row_hash            TEXT NOT NULL
    ) STRICT
    """,
    "CREATE INDEX ix_audit_log_ts ON audit_log(timestamp)",
    "CREATE INDEX ix_audit_log_action ON audit_log(action)",
    "CREATE INDEX ix_audit_log_manifest_run ON audit_log(manifest_run_id)",
    "CREATE INDEX ix_audit_log_permanode ON audit_log(permanode_id)",
    # Append-only triggers — UPDATE + DELETE both raise. Without these,
    # an operator with sqlite3 CLI could tamper silently. With them, any
    # mutation aborts the transaction.
    """
    CREATE TRIGGER trg_audit_log_no_update
    BEFORE UPDATE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit_log is append-only');
    END
    """,
    """
    CREATE TRIGGER trg_audit_log_no_delete
    BEFORE DELETE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit_log is append-only');
    END
    """,
    # ── legacy_import_log ───────────────────────────────────────────────────
    """
    CREATE TABLE legacy_import_log (
        id                  INTEGER PRIMARY KEY,
        imported_at         TEXT NOT NULL,
        source_db_path      TEXT NOT NULL,
        source_db_sha256    TEXT NOT NULL,
        rows_read           INTEGER NOT NULL,
        rows_inserted       INTEGER NOT NULL,
        rows_skipped        INTEGER NOT NULL,
        notes               TEXT
    ) STRICT
    """,
    # ── meta ────────────────────────────────────────────────────────────────
    """
    CREATE TABLE meta (
        key                 TEXT PRIMARY KEY,
        value               TEXT NOT NULL,
        updated_at          TEXT NOT NULL
    ) STRICT
    """,
]


def upgrade() -> None:
    for stmt in _DDL_STATEMENTS:
        op.execute(stmt)
    # Seed meta with the schema_version + machine_id placeholder. The
    # machine_id is filled in by ``steward db migrate`` at the application
    # layer (it needs ``platformdirs.user_data_path`` for stability across
    # reboots, which isn't appropriate inside an alembic migration body).
    op.execute("INSERT INTO meta (key, value, updated_at) VALUES ('schema_version', '0001_initial', datetime('now'))")


def downgrade() -> None:
    # Downgrade exists for completeness but Steward intentionally never
    # downgrades the inventory DB: schema-breaking changes require an
    # explicit migration (see ADR-0008 on machine_id retrofit risk).
    for table in (
        "meta",
        "legacy_import_log",
        "embeddings_vec",
        "embeddings",
        "tiers",
        "hashes",
        "claims",
        "scan_runs",
        "permanodes",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP TABLE IF EXISTS audit_log")
