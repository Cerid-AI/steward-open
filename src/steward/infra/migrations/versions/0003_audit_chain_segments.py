"""audit_chain_segments — registry for sealed audit archives (ADR-0018 phase A)

Revision ID: 0003_audit_chain_segments
Revises: 0002_attached_inventories
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_audit_chain_segments"
down_revision: str | None = "0002_attached_inventories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DDL_STATEMENTS = [
    """
    CREATE TABLE audit_chain_segments (
        id              INTEGER PRIMARY KEY,
        sealed_at       TEXT NOT NULL,
        first_id        INTEGER NOT NULL,
        through_id      INTEGER NOT NULL,
        row_count       INTEGER NOT NULL,
        tip_hash        TEXT NOT NULL,
        prior_tip_hash  TEXT,
        segment_relpath TEXT NOT NULL,
        segment_blake3  TEXT NOT NULL,
        shrunk_at       TEXT,
        audit_row_id    INTEGER NOT NULL
    ) STRICT
    """,
    "CREATE INDEX ix_audit_chain_segments_through ON audit_chain_segments(through_id)",
]


def upgrade() -> None:
    for stmt in _DDL_STATEMENTS:
        op.execute(stmt)
    op.execute(
        "INSERT OR REPLACE INTO meta (key, value, updated_at) VALUES "
        "('schema_version', '0003_audit_chain_segments', datetime('now'))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_chain_segments")
