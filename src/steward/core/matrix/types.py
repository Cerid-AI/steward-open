# SPDX-License-Identifier: Apache-2.0
"""I/O-free data-matrix types (ADR-0022)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DimensionKey = Literal[
    "tier",
    "domain",
    "volume",
    "extension",
    "classification",
    "machine_id",
    "source",
]

MeasureKey = Literal["total_bytes", "claim_count", "permanode_count"]

OverlayKey = Literal["none", "domain", "extension", "tier", "source"]

HIGH_CARDINALITY: frozenset[str] = frozenset({"extension", "classification", "machine_id"})

DEFAULT_CROSS_LIMIT = 50
MAX_CROSS_LIMIT = 500
DEFAULT_CHILD_LIMIT = 100
MAX_CHILD_LIMIT = 500

# SQL column for each dimension; source is derived at query time.
DIMENSION_COLUMNS: dict[str, str | None] = {
    "tier": "tier",
    "domain": "domain",
    "volume": "volume",
    "extension": "extension",
    "classification": "classification",
    "machine_id": "machine_id",
    "source": None,
}


@dataclass(frozen=True, slots=True)
class CrossStatsRequest:
    dim_a: DimensionKey
    dim_b: DimensionKey | None = None
    measure: MeasureKey = "total_bytes"
    path_prefix: str | None = None
    filters: dict[str, str] = field(default_factory=dict)
    limit: int = DEFAULT_CROSS_LIMIT
    include_imports: bool = False


@dataclass(frozen=True, slots=True)
class CrossStatsCell:
    a: str | None
    b: str | None
    claim_count: int
    permanode_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class CrossStatsResult:
    generated_at: str
    dim_a: DimensionKey
    dim_b: DimensionKey | None
    measure: MeasureKey
    cells: tuple[CrossStatsCell, ...]
    truncated: bool
    include_imports: bool
    path_prefix: str | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PathTreeRequest:
    path_prefix: str = ""
    measure: MeasureKey = "total_bytes"
    color_by: OverlayKey = "none"
    child_limit: int = DEFAULT_CHILD_LIMIT
    include_imports: bool = False
    tier: str | None = None
    volume: str | None = None


@dataclass(frozen=True, slots=True)
class PathTreeNode:
    path: str
    name: str
    is_dir: bool
    claim_count: int
    permanode_count: int
    total_bytes: int
    overlay_value: str | None = None


@dataclass(frozen=True, slots=True)
class PathTreeResult:
    generated_at: str
    path_prefix: str
    measure: MeasureKey
    color_by: OverlayKey
    children: tuple[PathTreeNode, ...]
    truncated: bool
    include_imports: bool
    notes: tuple[str, ...] = ()
