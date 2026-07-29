# SPDX-License-Identifier: Apache-2.0

"""Load + validate a Steward policy YAML.

The loader does three things:

1. ``yaml.safe_load`` the file.
2. Dispatch on ``kind`` to the matching pydantic model.
3. Return the typed instance (or raise :class:`PolicyError`).

A policy YAML must declare both ``version: 1`` and a ``kind`` field.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from steward.core.errors import PolicyError
from steward.core.policy.schema import (
    ArchivePolicy,
    ClassificationPolicy,
    PromotionPolicy,
    ReplicationPolicy,
    RetentionPolicy,
)

_KINDS: dict[
    str,
    type[ArchivePolicy]
    | type[ClassificationPolicy]
    | type[PromotionPolicy]
    | type[ReplicationPolicy]
    | type[RetentionPolicy],
] = {
    "ArchivePolicy": ArchivePolicy,
    "ClassificationPolicy": ClassificationPolicy,
    "PromotionPolicy": PromotionPolicy,
    "ReplicationPolicy": ReplicationPolicy,
    "RetentionPolicy": RetentionPolicy,
}

PolicyType = ArchivePolicy | ClassificationPolicy | PromotionPolicy | ReplicationPolicy | RetentionPolicy


def load_policy_from_text(text: str) -> PolicyType:
    """Parse + validate a policy YAML supplied as text."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError(f"YAML parse error: {exc}") from exc
    return _validate(data)


def load_policy(path: Path) -> PolicyType:
    """Parse + validate a policy YAML at ``path``."""
    if not path.exists():
        raise PolicyError(f"Policy file not found: {path}")
    return load_policy_from_text(path.read_text(encoding="utf-8"))


def _validate(data: Any) -> PolicyType:
    if not isinstance(data, dict):
        raise PolicyError(f"Policy root must be a mapping, got {type(data).__name__}")
    version = data.get("version")
    if version != 1:
        raise PolicyError(f"Unsupported policy version: {version!r} (expected 1)")
    kind = data.get("kind")
    if not isinstance(kind, str):
        raise PolicyError(f"Policy missing string 'kind' field; got {kind!r}")
    model_cls = _KINDS.get(kind)
    if model_cls is None:
        raise PolicyError(f"Unknown policy kind: {kind!r}")
    try:
        return model_cls(**data)
    except ValidationError as exc:
        raise PolicyError(f"Invalid {kind}: {exc}") from exc
