# SPDX-License-Identifier: Apache-2.0

"""Permanode — the deduplication identity.

One permanode == one piece of content. Same bytes anywhere, anytime, on
any machine collapse to the same permanode. A permanode's id is derived
from ``(canonical_hash, size_bytes)`` — see :mod:`steward.core.ids`.

A permanode is *not* a location on disk. Locations are :class:`Claim`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Permanode(BaseModel):
    """The deduplication identity row."""

    model_config = ConfigDict(frozen=True, strict=True)

    id: str = Field(min_length=32, max_length=32)
    canonical_hash: str = Field(min_length=64)
    canonical_hash_algo: str = "blake3"
    size_bytes: int = Field(ge=0)
    first_seen_at: datetime
    last_seen_at: datetime
