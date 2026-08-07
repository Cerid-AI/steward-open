# SPDX-License-Identifier: Apache-2.0
"""Data matrix + inventory surface pure types (ADR-0022)."""

from steward.core.matrix.path_segments import (
    child_path,
    leaf_name_under_prefix,
    next_segment,
    normalize_prefix,
)
from steward.core.matrix.types import (
    DEFAULT_CHILD_LIMIT,
    DEFAULT_CROSS_LIMIT,
    DIMENSION_COLUMNS,
    HIGH_CARDINALITY,
    MAX_CHILD_LIMIT,
    MAX_CROSS_LIMIT,
    CrossStatsCell,
    CrossStatsRequest,
    CrossStatsResult,
    DimensionKey,
    MeasureKey,
    OverlayKey,
    PathTreeNode,
    PathTreeRequest,
    PathTreeResult,
)
from steward.core.matrix.validate import MatrixValidationError, validate_cross, validate_path_tree

__all__ = [
    "DEFAULT_CHILD_LIMIT",
    "DEFAULT_CROSS_LIMIT",
    "DIMENSION_COLUMNS",
    "HIGH_CARDINALITY",
    "MAX_CHILD_LIMIT",
    "MAX_CROSS_LIMIT",
    "CrossStatsCell",
    "CrossStatsRequest",
    "CrossStatsResult",
    "DimensionKey",
    "MatrixValidationError",
    "MeasureKey",
    "OverlayKey",
    "PathTreeNode",
    "PathTreeRequest",
    "PathTreeResult",
    "child_path",
    "leaf_name_under_prefix",
    "next_segment",
    "normalize_prefix",
    "validate_cross",
    "validate_path_tree",
]
