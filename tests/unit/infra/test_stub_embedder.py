# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the deterministic StubEmbedder."""
from __future__ import annotations

import math

from steward.core.embed import EMBEDDING_DIMENSION, EmbedRequest
from steward.infra.embed.stub import StubEmbedder


def test_stub_produces_correct_dimension() -> None:
    e = StubEmbedder().embed(EmbedRequest(permanode_id="p", text="hello"))
    assert len(e.vector) == EMBEDDING_DIMENSION


def test_stub_is_deterministic() -> None:
    a = StubEmbedder().embed(EmbedRequest(permanode_id="p", text="hello"))
    b = StubEmbedder().embed(EmbedRequest(permanode_id="q", text="hello"))
    # Same text → same vector regardless of permanode_id.
    assert a.vector == b.vector


def test_stub_changes_with_text() -> None:
    a = StubEmbedder().embed(EmbedRequest(permanode_id="p", text="cats"))
    b = StubEmbedder().embed(EmbedRequest(permanode_id="p", text="dogs"))
    assert a.vector != b.vector


def test_stub_vector_is_unit_norm() -> None:
    e = StubEmbedder().embed(EmbedRequest(permanode_id="p", text="x"))
    norm = math.sqrt(sum(v * v for v in e.vector))
    assert abs(norm - 1.0) < 1e-5


def test_stub_info_identifies_backend() -> None:
    info = StubEmbedder().info
    assert info.model_name == "stub"
    assert info.model_version == "blake2b-v1"
    assert info.dimension == EMBEDDING_DIMENSION


def test_stub_embed_batch_matches_one_at_a_time() -> None:
    embedder = StubEmbedder()
    reqs = [
        EmbedRequest(permanode_id="a", text="alpha"),
        EmbedRequest(permanode_id="b", text="beta"),
    ]
    batch = embedder.embed_batch(reqs)
    one_by_one = [embedder.embed(r) for r in reqs]
    assert [b.vector for b in batch] == [o.vector for o in one_by_one]
