# SPDX-License-Identifier: Apache-2.0

"""Persistence layer for embeddings — writes both ``embeddings`` rows and
the ``embeddings_vec`` virtual table together.

A write is two SQL statements wrapped in the caller's transaction:

1. ``INSERT OR REPLACE INTO embeddings(...)`` — keyed by
   ``(permanode_id, model_name, model_version)``. ``OR REPLACE`` is
   used so a re-embed with a newer model version simply overwrites
   the prior row.
2. ``INSERT OR REPLACE INTO embeddings_vec(...)`` — keyed by the
   ``embedding_id`` returned in step 1.

The two stay in lockstep: every ``embeddings`` row has exactly one
``embeddings_vec`` row, and vice versa.

The bulk helper :func:`embed_permanodes_batch` walks permanodes in
batches, builds canonical input text via
:func:`steward.core.embed.build_permanode_text`, runs the embedder, and
writes the result. It returns a small :class:`BatchEmbedReport` rather
than printing, so the CLI can format the summary.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from steward.core.embed import (
    EmbedderProtocol,
    Embedding,
    EmbedRequest,
    build_permanode_text,
    to_blob,
)


@dataclass
class BatchEmbedReport:
    """Summary returned by :func:`embed_permanodes_batch`."""

    candidates: int  # permanodes considered
    embedded: int  # rows actually written
    skipped_existing: int  # already had a row for this (model_name, model_version)
    errors: int


def write_embedding(con: sqlite3.Connection, *, emb: Embedding) -> int:
    """Persist one embedding. Returns the ``embeddings.id`` it wrote.

    Caller owns the transaction. The function is idempotent: re-running
    with the same ``(permanode_id, model_name, model_version)`` updates
    the row's ``computed_at`` and the corresponding vec0 row.
    """
    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Look up an existing row so we can keep its id stable (the vec0
    # virtual table is keyed on this id).
    existing = con.execute(
        """
        SELECT id FROM embeddings
        WHERE permanode_id = ? AND model_name = ? AND model_version = ?
        """,
        (emb.permanode_id, emb.info.model_name, emb.info.model_version),
    ).fetchone()

    if existing is not None:
        row_id = int(existing[0])
        con.execute(
            """
            UPDATE embeddings
            SET dimension = ?, computed_at = ?
            WHERE id = ?
            """,
            (emb.info.dimension, computed_at, row_id),
        )
    else:
        cur = con.execute(
            """
            INSERT INTO embeddings
                (permanode_id, model_name, model_version, dimension, computed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                emb.permanode_id,
                emb.info.model_name,
                emb.info.model_version,
                emb.info.dimension,
                computed_at,
            ),
        )
        row_id = int(cur.lastrowid or 0)

    blob = to_blob(emb.vector)
    # vec0 doesn't support INSERT OR REPLACE on its primary key —
    # delete the prior row (no-op if absent) and insert fresh.
    con.execute("DELETE FROM embeddings_vec WHERE embedding_id = ?", (row_id,))
    con.execute(
        "INSERT INTO embeddings_vec (embedding_id, embedding) VALUES (?, ?)",
        (row_id, blob),
    )
    return row_id


def _select_candidate_permanodes(
    con: sqlite3.Connection,
    *,
    model_name: str,
    model_version: str,
    limit: int | None,
    reembed_all: bool,
) -> list[tuple[str, str, str, str, str, str]]:
    """Return ``(permanode_id, basename, parent_dir, classification, domain, tier)``
    tuples for permanodes that don't yet have an embedding for
    ``(model_name, model_version)``.

    Only the most-canonical claim per permanode is considered. Ordering
    is deterministic: by ``permanodes.id`` ascending.
    """
    base_select = """
        SELECT p.id, c.basename, c.parent_dir,
               COALESCE(c.classification, ''),
               COALESCE(c.domain, ''),
               COALESCE(c.tier, '')
        FROM permanodes p
        JOIN claims c ON c.permanode_id = p.id AND c.is_current = 1
    """
    if reembed_all:
        where = ""
        params: tuple[object, ...] = ()
    else:
        where = """
            WHERE NOT EXISTS (
                SELECT 1 FROM embeddings e
                WHERE e.permanode_id = p.id
                  AND e.model_name = ?
                  AND e.model_version = ?
            )
        """
        params = (model_name, model_version)

    sql = base_select + where + " GROUP BY p.id ORDER BY p.id"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    rows = con.execute(sql, params).fetchall()
    return [(str(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4]), str(r[5])) for r in rows]


def embed_permanodes_batch(
    con: sqlite3.Connection,
    *,
    embedder: EmbedderProtocol,
    limit: int | None = None,
    reembed_all: bool = False,
    batch_size: int = 32,
) -> BatchEmbedReport:
    """Embed permanodes lacking a row for the embedder's
    ``(model_name, model_version)``.

    Caller owns the transaction. When ``reembed_all`` is True, every
    permanode is re-embedded regardless of existing rows (the writer's
    upsert semantics handle the replacement).
    """
    info = embedder.info
    candidates = _select_candidate_permanodes(
        con,
        model_name=info.model_name,
        model_version=info.model_version,
        limit=limit,
        reembed_all=reembed_all,
    )

    report = BatchEmbedReport(
        candidates=len(candidates),
        embedded=0,
        skipped_existing=0,
        errors=0,
    )

    # Buffer requests so we can hand the embedder mini-batches.
    buffer: list[EmbedRequest] = []
    pending_pids: list[str] = []

    def _flush() -> None:
        if not buffer:
            return
        results = embedder.embed_batch(buffer)
        for emb in results:
            try:
                write_embedding(con, emb=emb)
                report.embedded += 1
            except sqlite3.DatabaseError:
                report.errors += 1
        buffer.clear()
        pending_pids.clear()

    for pid, basename, parent_dir, classification, domain, tier in candidates:
        text = build_permanode_text(
            basename=basename,
            parent_dir=parent_dir or None,
            classification=classification or None,
            domain=domain or None,
            tier=tier or None,
        )
        buffer.append(EmbedRequest(permanode_id=pid, text=text))
        pending_pids.append(pid)
        if len(buffer) >= batch_size:
            _flush()
    _flush()

    return report


__all__ = [
    "BatchEmbedReport",
    "embed_permanodes_batch",
    "write_embedding",
]
