"""attached_inventories — cross-machine inventory mount table (ADR-0013)

Revision ID: 0002_attached_inventories
Revises: 0001_initial
Create Date: 2026-05-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_attached_inventories"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Single new table. The wire-format design (ADR-0013) explicitly keeps
# imported inventories OUT of the existing tables — they live as
# separate SQLite files mounted via ATTACH DATABASE. This row tracks
# which ones are currently attached and where their .db files live.


_DDL_STATEMENTS = [
    """
    CREATE TABLE attached_inventories (
        machine_id          TEXT PRIMARY KEY,
        file_path           TEXT NOT NULL,
        imported_at         TEXT NOT NULL,
        exporter_version    TEXT NOT NULL,
        exporter_hostname   TEXT,
        payload_blake3      TEXT NOT NULL,
        audit_rows          INTEGER NOT NULL,
        chain_verified_at   TEXT,
        notes               TEXT
    ) STRICT
    """,
    "CREATE INDEX ix_attached_inventories_imported_at ON attached_inventories(imported_at)",
]


def upgrade() -> None:
    for stmt in _DDL_STATEMENTS:
        op.execute(stmt)
    # Bump meta.schema_version so `steward db migrate` reports the new revision.
    op.execute(
        "INSERT OR REPLACE INTO meta (key, value, updated_at) VALUES "
        "('schema_version', '0002_attached_inventories', datetime('now'))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_attached_inventories_imported_at")
    op.execute("DROP TABLE IF EXISTS attached_inventories")
    op.execute(
        "INSERT OR REPLACE INTO meta (key, value, updated_at) VALUES "
        "('schema_version', '0001_initial', datetime('now'))"
    )
