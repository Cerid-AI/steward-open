# SPDX-License-Identifier: Apache-2.0

"""Plan manifest — the artefact ``steward policy plan`` produces and
``steward apply`` consumes.

A manifest is a TSV-shaped sequence of rows plus a versioned header. The
header carries (a) the Steward version that produced it (so a stale
manifest from an older version can be refused) and (b) the policy name
and phase that generated it. The rows are typed via :class:`ManifestRow`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ActionKind = Literal[
    "promote",  # copy from source tier to destination tier (verify-by-hash)
    "stash",  # same-FS rename into _cooling-off-stash/<run_id>/
    "nas_manifest",  # emit retire instructions for a read-only NAS tier
    "restore",  # restore from cooling-off-stash back to original location
    "finalize_stash",  # rm a stash entry after cooling-off has elapsed
    "reclassify",  # update claim classification (no FS mutation)
    "retire_direct",  # rm-in-place; cooling-off lives in the tier's external trash (ADR-0014)
]


class ManifestHeader(BaseModel):
    """The version + provenance preamble of a plan manifest."""

    model_config = ConfigDict(frozen=True, strict=True)

    produced_by_steward_version: str
    produced_at: datetime
    policy_name: str
    phase_name: str | None = None
    manifest_run_id: str
    """Stable identifier tying every audit row in one apply back to this plan."""


class ManifestRow(BaseModel):
    """One actionable row in a plan manifest."""

    model_config = ConfigDict(frozen=True, strict=True)

    action: ActionKind
    permanode_id: str = Field(min_length=32, max_length=32)
    canonical_hash: str
    size_bytes: int = Field(ge=0)
    source_path: str
    source_tier: str
    destination_path: str | None = None
    destination_tier: str | None = None
    rationale: str
    """Human-readable explanation of why this row was produced; copied verbatim
    into the corresponding audit row's ``payload_json``."""


class Manifest(BaseModel):
    """A complete manifest = header + zero or more rows."""

    model_config = ConfigDict(frozen=True, strict=True)

    header: ManifestHeader
    rows: tuple[ManifestRow, ...] = ()
