# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from steward.core.matrix.path_segments import (
    child_path,
    leaf_name_under_prefix,
    next_segment,
    normalize_prefix,
)
from steward.core.matrix.types import CrossStatsRequest, PathTreeRequest
from steward.core.matrix.validate import MatrixValidationError, validate_cross, validate_path_tree


def test_validate_cross_rejects_same_dims() -> None:
    with pytest.raises(MatrixValidationError, match="dim_a"):
        validate_cross(CrossStatsRequest(dim_a="tier", dim_b="tier"))


def test_validate_cross_clamps_limit() -> None:
    req = validate_cross(CrossStatsRequest(dim_a="extension", limit=9999))
    assert req.limit == 500


def test_validate_cross_rejects_unknown_filter_key() -> None:
    with pytest.raises(MatrixValidationError, match="filter"):
        validate_cross(CrossStatsRequest(dim_a="tier", filters={"nope": "x"}))


def test_validate_cross_source_requires_imports() -> None:
    with pytest.raises(MatrixValidationError, match="include_imports"):
        validate_cross(CrossStatsRequest(dim_a="source", include_imports=False))


def test_normalize_prefix_strips_trailing_slash() -> None:
    assert normalize_prefix("/Volumes/Data/") == "/Volumes/Data"
    assert normalize_prefix("") == ""


def test_next_segment_under_prefix() -> None:
    assert next_segment("/a/b/c.txt", "/a") == "b"
    # Direct child path (no further slash) is a leaf, not a dir segment.
    assert next_segment("/a/b", "/a") is None
    assert next_segment("/a/file.txt", "/a") is None


def test_leaf_name_under_prefix() -> None:
    assert leaf_name_under_prefix("/a/file.txt", "/a") == "file.txt"
    assert leaf_name_under_prefix("/a/b/c.txt", "/a") is None


def test_child_path() -> None:
    assert child_path("/a", "b") == "/a/b"
    assert child_path("", "Volumes") in ("/Volumes", "Volumes")


def test_validate_path_tree_clamps_child_limit() -> None:
    req = validate_path_tree(PathTreeRequest(child_limit=9999))
    assert req.child_limit == 500
