# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the ReplicationPolicy schema + loader."""

from __future__ import annotations

import pytest

from steward.core.errors import PolicyError
from steward.core.policy.loader import load_policy_from_text
from steward.core.policy.schema import (
    ReplicationDefaults,
    ReplicationPolicy,
    ReplicationSource,
)

_MINIMAL = """\
version: 1
kind: ReplicationPolicy
sources:
  - name: invdb
    source: /a/inventory.db
    destination: /b/inventory.db
"""

_FULL = """\
version: 1
kind: ReplicationPolicy
metadata:
  name: test
defaults:
  rclone_bin: rclone
  timeout_seconds: 300
  transfers: 2
  checkers: 4
  extra_args: ["--bwlimit", "10M"]
sources:
  - name: invdb
    source: /a/inventory.db
    destination: /b/inventory.db
    mode: copy
    excludes: ["*.tmp"]
    includes: ["*.db"]
    enabled: true
  - name: workspace
    source: /a/workspace/
    destination: backup:steward/workspace/
    mode: sync
    enabled: false
"""


def test_minimal_loads_with_defaults() -> None:
    p = load_policy_from_text(_MINIMAL)
    assert isinstance(p, ReplicationPolicy)
    assert p.defaults.rclone_bin == "rclone"
    assert p.defaults.transfers == 4
    assert p.defaults.checkers == 8
    assert p.defaults.timeout_seconds == 3600
    assert len(p.sources) == 1
    src = p.sources[0]
    assert src.name == "invdb"
    assert src.mode == "copy"
    assert src.enabled is True
    assert src.excludes == []


def test_full_loads_and_carries_every_field() -> None:
    p = load_policy_from_text(_FULL)
    assert isinstance(p, ReplicationPolicy)
    assert p.defaults.timeout_seconds == 300
    assert p.defaults.transfers == 2
    assert p.defaults.extra_args == ["--bwlimit", "10M"]
    by_name = {s.name: s for s in p.sources}
    assert by_name["invdb"].includes == ["*.db"]
    assert by_name["invdb"].excludes == ["*.tmp"]
    assert by_name["workspace"].mode == "sync"
    assert by_name["workspace"].enabled is False


def test_unknown_field_rejected() -> None:
    """``extra = "forbid"`` on the base model: typos in YAML must fail loudly."""
    bad = """\
version: 1
kind: ReplicationPolicy
sources:
  - name: x
    source: /a
    destination: /b
    typo_field: oops
"""
    with pytest.raises(PolicyError):
        load_policy_from_text(bad)


def test_invalid_mode_rejected() -> None:
    bad = """\
version: 1
kind: ReplicationPolicy
sources:
  - name: x
    source: /a
    destination: /b
    mode: ohno
"""
    with pytest.raises(PolicyError):
        load_policy_from_text(bad)


def test_timeout_floor() -> None:
    """``timeout_seconds`` is constrained to >= 60 — a sub-minute timeout
    is almost certainly an authoring error rather than a real intent."""
    bad = """\
version: 1
kind: ReplicationPolicy
defaults:
  timeout_seconds: 30
sources:
  - name: x
    source: /a
    destination: /b
"""
    with pytest.raises(PolicyError):
        load_policy_from_text(bad)


def test_defaults_constructor_round_trip() -> None:
    """The pydantic defaults match the YAML defaults for the bundled
    policy — operator copy-paste won't surprise them."""
    d = ReplicationDefaults()
    assert d.rclone_bin == "rclone"
    assert d.transfers == 4
    assert d.checkers == 8


def test_source_required_fields() -> None:
    """``name`` / ``source`` / ``destination`` are required."""
    with pytest.raises(Exception):
        ReplicationSource()  # type: ignore[call-arg]
