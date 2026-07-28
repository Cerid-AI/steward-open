# SPDX-License-Identifier: Apache-2.0

"""Semantic search — query a vec0 virtual table for nearest permanodes.

Operator-facing through ``steward search``. Given a free-text query, we:

1. Embed the query with the same embedder that wrote ``embeddings``.
2. Run a vec0 KNN against ``embeddings_vec`` filtered to that model's
   row ids.
3. Return :class:`SearchResult` items containing the permanode id, the
   most-canonical claim's path, and the cosine distance.

If the operator's query is embedded by a *different* model than the
stored rows, the recall would be meaningless — the writer's
``(model_name, model_version)`` filter prevents that mismatch.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from steward.core.embed import (
    EmbedderProtocol,
    EmbedRequest,
    to_blob,
)


@dataclass(frozen=True, slots=True)
class SearchResult:
    permanode_id: str
    canonical_path: str
    canonical_hash: str
    distance: float


def semantic_search(
    con: sqlite3.Connection,
    *,
    embedder: EmbedderProtocol,
    query: str,
    k: int = 10,
) -> list[SearchResult]:
    """Return the top-``k`` permanodes whose embeddings are closest to ``query``.

    Distances are cosine (vec0's default). Lower is better. Results are
    deterministic; ties broken by permanode id ascending.
    """
    # Embed the query.
    info = embedder.info
    q_emb = embedder.embed(EmbedRequest(permanode_id="(query)", text=query))
    blob = to_blob(q_emb.vector)

    # Pull eligible embedding_ids first, then KNN inside that set.
    # We need to scope vec0 results to rows from the same (model_name,
    # model_version). vec0 doesn't support arbitrary WHERE on companion
    # columns, so we join against the SQL table on embedding_id.
    sql = """
        SELECT p.id, p.canonical_hash, c.file_path, vec.distance
        FROM (
            SELECT embedding_id, distance
            FROM embeddings_vec
            WHERE embedding MATCH ?
              AND k = ?
        ) AS vec
        JOIN embeddings e ON e.id = vec.embedding_id
        JOIN permanodes p ON p.id = e.permanode_id
        JOIN claims c     ON c.permanode_id = p.id AND c.is_current = 1
        WHERE e.model_name = ? AND e.model_version = ?
        GROUP BY p.id
        ORDER BY vec.distance ASC, p.id ASC
        LIMIT ?
    """
    rows = con.execute(
        sql,
        (blob, k * 3, info.model_name, info.model_version, k),
    ).fetchall()

    return [
        SearchResult(
            permanode_id=str(r[0]),
            canonical_hash=str(r[1]),
            canonical_path=str(r[2]),
            distance=float(r[3]),
        )
        for r in rows
    ]


__all__ = ["SearchResult", "semantic_search"]
