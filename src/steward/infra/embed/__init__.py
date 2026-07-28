"""Concrete embedder implementations + persistence layer.

Importing this package does not eagerly import ONNX runtime — the heavy
imports live inside :mod:`steward.infra.embed.onnx` and only run when
the operator explicitly chooses that backend.
"""

from steward.infra.embed.search import SearchResult, semantic_search
from steward.infra.embed.stub import StubEmbedder
from steward.infra.embed.writer import (
    BatchEmbedReport,
    embed_permanodes_batch,
    write_embedding,
)

__all__ = [
    "BatchEmbedReport",
    "SearchResult",
    "StubEmbedder",
    "embed_permanodes_batch",
    "semantic_search",
    "write_embedding",
]
