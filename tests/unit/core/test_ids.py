# SPDX-License-Identifier: Apache-2.0

"""Tests for :func:`steward.core.ids.permanode_id`."""
from __future__ import annotations

import pytest

from steward.core.ids import permanode_id


def test_deterministic() -> None:
    h = "a" * 64
    assert permanode_id(h, 1234) == permanode_id(h, 1234)


def test_length_is_32() -> None:
    assert len(permanode_id("abc", 0)) == 32


def test_case_insensitive() -> None:
    h_upper = "ABC123"
    h_lower = h_upper.lower()
    assert permanode_id(h_upper, 7) == permanode_id(h_lower, 7)


def test_whitespace_normalized() -> None:
    assert permanode_id("  abc\n", 7) == permanode_id("abc", 7)


def test_distinct_size_distinct_id() -> None:
    h = "abc"
    assert permanode_id(h, 1) != permanode_id(h, 2)


def test_distinct_hash_distinct_id() -> None:
    assert permanode_id("aaa", 1) != permanode_id("bbb", 1)


def test_colon_avoids_collision() -> None:
    # The ``hash || ":" || size`` delimiter prevents this canonical collision:
    # ("xyz",  12)  vs  ("xyz12", "")
    a = permanode_id("xyz", 12)
    b = permanode_id("xyz12", 0)
    assert a != b


def test_empty_hash_raises() -> None:
    with pytest.raises(ValueError):
        permanode_id("", 1)


def test_negative_size_raises() -> None:
    with pytest.raises(ValueError):
        permanode_id("abc", -1)
