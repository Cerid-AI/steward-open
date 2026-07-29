# SPDX-License-Identifier: Apache-2.0

"""Claim — an observation of a permanode at a path on a machine.

One row per ``(permanode, machine, file_path, container_path, scan_run_id)``.
Steward's append-only model means a moved file produces a new claim with
``is_current=1`` and the prior claim flips to ``is_current=0`` in the same
transaction — the history is preserved.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Claim(BaseModel):
    """One row in the claims table."""

    model_config = ConfigDict(frozen=True, strict=True)

    id: int | None = None
    permanode_id: str = Field(min_length=32, max_length=32)
    machine_id: str
    file_path: str
    parent_dir: str
    basename: str
    extension: str | None = None
    tier: str
    volume: str
    domain: str | None = None
    classification: str | None = None
    container_path: str | None = None
    container_sha256: str | None = None
    size_bytes: int = Field(ge=0)
    mtime_iso: str | None = None
    observed_at: datetime
    scan_run_id: int
    is_current: bool = True
    legacy_sha256: str | None = None
