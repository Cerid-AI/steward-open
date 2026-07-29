# SPDX-License-Identifier: Apache-2.0

"""Orchestration facade for the embed CLI subcommands.

Encapsulates the connect/commit lifecycle so the CLI doesn't import
``infra.db.connect`` (import-linter contract). Mirrors
:mod:`steward.infra.scanner.orchestrate`.
"""

from __future__ import annotations

from pathlib import Path

from steward.core.embed import EmbedderProtocol
from steward.infra.db.connect import connect
from steward.infra.embed.search import SearchResult, semantic_search
from steward.infra.embed.writer import BatchEmbedReport, embed_permanodes_batch


def run_embed_batch(
    *,
    db_path: Path,
    embedder: EmbedderProtocol,
    limit: int | None = None,
    reembed_all: bool = False,
    batch_size: int = 32,
) -> BatchEmbedReport:
    """Open ``db_path``, run :func:`embed_permanodes_batch`, commit, return the report."""
    con = connect(db_path)
    try:
        report = embed_permanodes_batch(
            con,
            embedder=embedder,
            limit=limit,
            reembed_all=reembed_all,
            batch_size=batch_size,
        )
        con.commit()
    finally:
        con.close()
    return report


def run_semantic_search(
    *,
    db_path: Path,
    embedder: EmbedderProtocol,
    query: str,
    k: int = 10,
) -> list[SearchResult]:
    """Open ``db_path``, run :func:`semantic_search`, close, return results."""
    con = connect(db_path, read_only=True)
    try:
        return semantic_search(con, embedder=embedder, query=query, k=k)
    finally:
        con.close()


__all__ = ["run_embed_batch", "run_semantic_search"]
