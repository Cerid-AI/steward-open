# SPDX-License-Identifier: Apache-2.0

"""Tests for :mod:`steward.core.audit`."""

from __future__ import annotations

from steward.core.audit import GENESIS_PREV_HASH, canonical_payload, compute_row_hash


def test_genesis_prev_hash_is_64_hex() -> None:
    assert len(GENESIS_PREV_HASH) == 64
    assert all(c == "0" for c in GENESIS_PREV_HASH)


def test_canonical_payload_omits_derived_fields() -> None:
    row = {
        "id": 1,
        "action": "promote",
        "prev_hash": "abc",
        "row_hash": "def",
    }
    blob = canonical_payload(row)
    assert b"id" not in blob
    assert b"prev_hash" not in blob
    assert b"row_hash" not in blob
    assert b"action" in blob


def test_canonical_payload_is_byte_stable() -> None:
    # Key order in the input dict must not change the output bytes.
    a = canonical_payload({"x": 1, "y": 2, "z": 3})
    b = canonical_payload({"z": 3, "x": 1, "y": 2})
    assert a == b


def test_compute_row_hash_chains() -> None:
    payload = {"action": "scan_start"}
    h1 = compute_row_hash(GENESIS_PREV_HASH, payload)
    h2 = compute_row_hash(h1, payload)
    assert h1 != h2  # same payload, different prev_hash → different hash


def test_compute_row_hash_rejects_short_prev() -> None:
    import pytest

    with pytest.raises(ValueError):
        compute_row_hash("abc", {"x": 1})
