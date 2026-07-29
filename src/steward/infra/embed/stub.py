# SPDX-License-Identifier: Apache-2.0

"""Deterministic hash-based embedder — no model required.

The :class:`StubEmbedder` produces a stable 384-dim vector for any input
string using a blake3-seeded PRNG. The output is meaningless for
semantic similarity, but:

* it satisfies :class:`EmbedderProtocol` exactly,
* it never depends on a downloaded ONNX model,
* it makes test runs deterministic and fast.

CI uses this; production should use the ONNX-backed embedder.

The "model_version" is ``stub-blake3-v1`` so a search query at recall
time can detect a stub-versus-ONNX mismatch and abort cleanly rather
than returning meaningless results.
"""

from __future__ import annotations

import struct
from hashlib import blake2b

from steward.core.embed import (
    EMBEDDING_DIMENSION,
    EmbedderInfo,
    Embedding,
    EmbedRequest,
)


class StubEmbedder:
    """Deterministic non-semantic embedder for tests / fallback."""

    def __init__(self) -> None:
        self._info = EmbedderInfo(
            model_name="stub",
            model_version="blake2b-v1",
            dimension=EMBEDDING_DIMENSION,
        )

    @property
    def info(self) -> EmbedderInfo:
        return self._info

    def _vector_for(self, text: str) -> tuple[float, ...]:
        """Produce a stable, L2-normalised 384-dim vector from ``text``.

        We pull bytes from a blake2b digest expanded into 384 * 4 bytes,
        unpack as float32, and L2-normalise so cosine similarity (the
        vec0 default) returns sensible values.
        """
        out: list[float] = []
        block = 0
        # 384 floats * 4 bytes = 1536 bytes — we'll need 24 64-byte
        # blake2b digests to fill that. blake2b's max digest size is 64;
        # we vary the personalization to get independent blocks.
        while len(out) < EMBEDDING_DIMENSION:
            # blake2b `person` is hard-capped at 16 bytes — keep it tight.
            h = blake2b(
                text.encode("utf-8"),
                digest_size=64,
                person=f"stwd-{block:04d}".encode(),
            )
            buf = h.digest()
            for i in range(0, 64, 4):
                if len(out) >= EMBEDDING_DIMENSION:
                    break
                # int32 → float in [-1, 1]
                (v,) = struct.unpack("<i", buf[i : i + 4])
                out.append(v / 0x7FFFFFFF)
            block += 1
        # L2-normalise so cosine similarity works.
        norm = sum(x * x for x in out) ** 0.5 or 1.0
        return tuple(x / norm for x in out)

    def embed(self, request: EmbedRequest) -> Embedding:
        vec = self._vector_for(request.text)
        return Embedding(
            permanode_id=request.permanode_id,
            info=self._info,
            vector=vec,
        )

    def embed_batch(self, requests: list[EmbedRequest]) -> list[Embedding]:
        return [self.embed(r) for r in requests]


__all__ = ["StubEmbedder"]
