# SPDX-License-Identifier: Apache-2.0

"""Embedding domain — pure types and protocol; no I/O.

Defines the contract every concrete embedder must satisfy and a
canonical "embed-this-string" builder for permanodes. Concrete
implementations live under :mod:`steward.infra.embed`:

* :class:`steward.infra.embed.stub.StubEmbedder` — deterministic, no
  model needed; used by tests and as a fallback when no model is
  installed.
* :class:`steward.infra.embed.onnx.OnnxE5Embedder` — production
  implementation backed by ``onnxruntime`` + ``tokenizers``.

Vector dimension is fixed at module level (384) to match the
``embeddings_vec`` virtual table in schema 0001. Changing the dimension
requires a migration; see ADR-0008.
"""

from __future__ import annotations

import array
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

EMBEDDING_DIMENSION: int = 384


@dataclass(frozen=True, slots=True)
class EmbedderInfo:
    """Identity of the model that produced a vector.

    Persisted alongside each row in ``embeddings`` so a query at recall
    time can confirm it's reading from the same model space.
    """

    model_name: str
    model_version: str
    dimension: int = EMBEDDING_DIMENSION


@dataclass(frozen=True, slots=True)
class EmbedRequest:
    """One unit of work for an embedder.

    Attributes
    ----------
    permanode_id:
        The permanode whose representation is being computed.
    text:
        The canonical text input (see :func:`build_permanode_text`).
    """

    permanode_id: str
    text: str


@dataclass(frozen=True, slots=True)
class Embedding:
    """A computed vector.

    The vector is stored as a tuple of floats so the value object is
    hashable + cheap to compare in tests. Persistence layers
    materialize it back into a ``float[384]`` blob for sqlite-vec.
    """

    permanode_id: str
    info: EmbedderInfo
    vector: tuple[float, ...]


def to_blob(vector: tuple[float, ...]) -> bytes:
    """Pack a vector into the float32-little-endian byte blob ``vec0`` expects."""
    if len(vector) != EMBEDDING_DIMENSION:
        raise ValueError(f"vector dimension {len(vector)} != {EMBEDDING_DIMENSION}")
    return array.array("f", vector).tobytes()


def from_blob(blob: bytes) -> tuple[float, ...]:
    """Unpack a float32 blob back into a tuple of floats."""
    if len(blob) != EMBEDDING_DIMENSION * 4:
        raise ValueError(f"blob size {len(blob)} != {EMBEDDING_DIMENSION * 4} bytes")
    arr = array.array("f")
    arr.frombytes(blob)
    return tuple(arr)


def build_permanode_text(
    *,
    basename: str,
    parent_dir: str | None = None,
    classification: str | None = None,
    domain: str | None = None,
    tier: str | None = None,
) -> str:
    """Canonicalize a permanode into the text string fed to the embedder.

    The format is stable across model versions — when the model changes
    the *embeddings* get re-computed, but the input text stays the same
    so we don't pay for unnecessary re-embeds when only the model
    changes.

    The chosen order is "most discriminating first": basename →
    classification → domain → parent_dir → tier. The basename usually
    carries the most signal, classification next, and tier is least
    informative for semantic search (mostly path-prefix derived).
    """
    parts: list[str] = [basename.strip()]
    if classification:
        parts.append(classification.strip())
    if domain:
        parts.append(domain.strip())
    if parent_dir:
        parts.append(parent_dir.strip())
    if tier:
        parts.append(tier.strip())
    # Single space separator keeps tokenization simple regardless of model.
    return " ".join(p for p in parts if p)


@runtime_checkable
class EmbedderProtocol(Protocol):
    """Contract every embedder satisfies.

    An embedder is a stateful object — implementations may lazy-load
    a heavy ONNX session on first :meth:`embed` call. Callers should
    construct one embedder per :func:`steward.cli.embed_cmd` invocation.
    """

    @property
    def info(self) -> EmbedderInfo: ...

    def embed(self, request: EmbedRequest) -> Embedding: ...

    def embed_batch(self, requests: list[EmbedRequest]) -> list[Embedding]: ...


__all__ = [
    "EMBEDDING_DIMENSION",
    "Embedding",
    "EmbedderInfo",
    "EmbedderProtocol",
    "EmbedRequest",
    "build_permanode_text",
    "from_blob",
    "to_blob",
]
