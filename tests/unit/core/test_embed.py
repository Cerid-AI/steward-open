# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the embed domain — value objects, text builder, blob coding."""

from __future__ import annotations

import pytest

from steward.core.embed import (
    EMBEDDING_DIMENSION,
    EmbedderInfo,
    EmbedderProtocol,
    EmbedRequest,
    build_permanode_text,
    from_blob,
    to_blob,
)


def test_dimension_matches_schema() -> None:
    """Schema 0001's vec0 table is keyed on float[384]; the constant must match."""
    assert EMBEDDING_DIMENSION == 384


# ─────────────────────── blob round-trip ──────────────────────────


def test_blob_roundtrip_preserves_vector() -> None:
    vec = tuple(float(i) / 100.0 for i in range(EMBEDDING_DIMENSION))
    blob = to_blob(vec)
    assert len(blob) == EMBEDDING_DIMENSION * 4
    back = from_blob(blob)
    for a, b in zip(vec, back, strict=True):
        # float32 round-trip is not bit-exact for arbitrary floats.
        assert abs(a - b) < 1e-6


def test_blob_rejects_wrong_dim() -> None:
    with pytest.raises(ValueError, match="dimension"):
        to_blob((1.0, 2.0, 3.0))


def test_from_blob_rejects_wrong_size() -> None:
    with pytest.raises(ValueError, match="blob size"):
        from_blob(b"\x00" * 10)


# ─────────────────────── text builder ──────────────────────────


def test_build_permanode_text_basename_only() -> None:
    assert build_permanode_text(basename="photo.jpg") == "photo.jpg"


def test_build_permanode_text_full() -> None:
    out = build_permanode_text(
        basename="photo.jpg",
        parent_dir="/Volumes/L2/Photos/2024",
        classification="Family-2024",
        domain="photos",
        tier="L2",
    )
    # Basename first, then classification, then domain, then parent_dir, then tier.
    parts = out.split(" ")
    assert parts[0] == "photo.jpg"
    assert "Family-2024" in parts
    assert "photos" in parts
    assert parts[-1] == "L2"


def test_build_permanode_text_skips_empty_fields() -> None:
    # None and "" both drop out.
    out = build_permanode_text(
        basename="x",
        parent_dir="",
        classification=None,
        domain="docs",
    )
    assert out == "x docs"


# ─────────────────────── protocol satisfaction ──────────────────────────


def test_protocol_is_satisfied_by_minimal_class() -> None:
    """A class with the right shape passes the runtime Protocol check."""

    class _T:
        @property
        def info(self) -> EmbedderInfo:
            return EmbedderInfo(model_name="t", model_version="v")

        def embed(self, request: EmbedRequest) -> object:
            return request

        def embed_batch(self, requests: list[EmbedRequest]) -> list[object]:
            return list(requests)

    assert isinstance(_T(), EmbedderProtocol)
