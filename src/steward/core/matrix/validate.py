# SPDX-License-Identifier: Apache-2.0
"""Pure validation for ADR-0022 matrix requests."""

from __future__ import annotations

from steward.core.matrix.types import (
    DIMENSION_COLUMNS,
    MAX_CHILD_LIMIT,
    MAX_CROSS_LIMIT,
    CrossStatsRequest,
    DimensionKey,
    MeasureKey,
    OverlayKey,
    PathTreeRequest,
)

_ALLOWED_FILTERS = frozenset(DIMENSION_COLUMNS) | frozenset({"path_prefix"})
_MEASURES: frozenset[str] = frozenset({"total_bytes", "claim_count", "permanode_count"})
_OVERLAYS: frozenset[str] = frozenset(
    {"none", "domain", "extension", "tier", "source", "presence"}
)
# Bounded FS probes for color_by=presence (Wave C / ADR-0022).
DEFAULT_PRESENCE_PROBE_CAP = 200
MAX_PRESENCE_PROBE_CAP = 200


class MatrixValidationError(ValueError):
    """Invalid matrix / surface request."""


def _as_dim(value: str, label: str) -> DimensionKey:
    if value not in DIMENSION_COLUMNS:
        raise MatrixValidationError(f"unknown {label}: {value}")
    return value  # type: ignore[return-value]


def _as_measure(value: str) -> MeasureKey:
    if value not in _MEASURES:
        raise MatrixValidationError(f"unknown measure: {value}")
    return value  # type: ignore[return-value]


def _as_overlay(value: str) -> OverlayKey:
    if value not in _OVERLAYS:
        raise MatrixValidationError(f"unknown color_by: {value}")
    return value  # type: ignore[return-value]


def validate_cross(req: CrossStatsRequest) -> CrossStatsRequest:
    dim_a = _as_dim(str(req.dim_a), "dim_a")
    dim_b: DimensionKey | None = None
    if req.dim_b is not None:
        dim_b = _as_dim(str(req.dim_b), "dim_b")
        if dim_a == dim_b:
            raise MatrixValidationError("dim_a and dim_b must differ")
    measure = _as_measure(str(req.measure))
    for key in req.filters:
        if key not in _ALLOWED_FILTERS:
            raise MatrixValidationError(f"unknown filter key: {key}")
    if dim_a == "source" or dim_b == "source":
        if not req.include_imports:
            raise MatrixValidationError("source dimension requires include_imports=True")
    limit = max(1, min(int(req.limit), MAX_CROSS_LIMIT))
    return CrossStatsRequest(
        dim_a=dim_a,
        dim_b=dim_b,
        measure=measure,
        path_prefix=req.path_prefix,
        filters=dict(req.filters),
        limit=limit,
        include_imports=bool(req.include_imports),
    )


def validate_path_tree(req: PathTreeRequest) -> PathTreeRequest:
    measure = _as_measure(str(req.measure))
    color_by = _as_overlay(str(req.color_by))
    if color_by == "source" and not req.include_imports:
        raise MatrixValidationError("color_by=source requires include_imports=True")
    child_limit = max(1, min(int(req.child_limit), MAX_CHILD_LIMIT))
    return PathTreeRequest(
        path_prefix=req.path_prefix or "",
        measure=measure,
        color_by=color_by,
        child_limit=child_limit,
        include_imports=bool(req.include_imports),
        tier=req.tier,
        volume=req.volume,
    )
