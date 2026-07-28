# SPDX-License-Identifier: Apache-2.0

"""Tier registry row — declarative metadata for a storage tier."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Tier(BaseModel):
    """One row in the tiers table."""

    model_config = ConfigDict(frozen=True, strict=True)

    name: str
    priority: int = Field(ge=0)
    is_writable: bool
    stash_root: str | None = None
    path_prefixes: tuple[str, ...]
