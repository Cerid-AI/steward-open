# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the ArchivePolicy schema + loader."""
from __future__ import annotations

import pytest

from steward.core.errors import PolicyError
from steward.core.policy.loader import load_policy_from_text
from steward.core.policy.schema import (
    ArchiveDefaults,
    ArchivePolicy,
    ArchiveSource,
)

_MINIMAL = """\
version: 1
kind: ArchivePolicy
sources:
  - name: inv
    source: /a/inventory.db
    repository: /b/repo
"""

_FULL = """\
version: 1
kind: ArchivePolicy
metadata:
  name: test
defaults:
  restic_bin: /usr/local/bin/restic
  timeout_seconds: 300
  password_command: "echo hunter2"  # pragma: allowlist secret
  extra_args: ["--limit-upload", "10240"]
sources:
  - name: inv
    source: /a/inventory.db
    repository: /b/repo
    tags: ["nightly"]
    excludes: ["*.tmp"]
    exclude_caches: false
    enabled: true
  - name: workspace
    source: /a/workspace/
    repository: b2:bucket/path
    tags: ["weekly", "off-site"]
    enabled: false
"""


def test_minimal_loads_with_defaults() -> None:
    p = load_policy_from_text(_MINIMAL)
    assert isinstance(p, ArchivePolicy)
    assert p.defaults.restic_bin == "restic"
    assert p.defaults.timeout_seconds == 7200
    assert p.defaults.password_command is None
    assert p.defaults.password_file is None
    assert len(p.sources) == 1
    src = p.sources[0]
    assert src.exclude_caches is True
    assert src.enabled is True


def test_full_loads_and_carries_every_field() -> None:
    p = load_policy_from_text(_FULL)
    assert isinstance(p, ArchivePolicy)
    assert p.defaults.restic_bin == "/usr/local/bin/restic"
    assert p.defaults.password_command == "echo hunter2"  # pragma: allowlist secret
    assert p.defaults.extra_args == ["--limit-upload", "10240"]
    by_name = {s.name: s for s in p.sources}
    assert by_name["inv"].tags == ["nightly"]
    assert by_name["inv"].excludes == ["*.tmp"]
    assert by_name["inv"].exclude_caches is False
    assert by_name["workspace"].repository.startswith("b2:")
    assert by_name["workspace"].enabled is False


def test_unknown_field_rejected() -> None:
    bad = """\
version: 1
kind: ArchivePolicy
sources:
  - name: x
    source: /a
    repository: /b
    typo: oops
"""
    with pytest.raises(PolicyError):
        load_policy_from_text(bad)


def test_timeout_floor() -> None:
    bad = """\
version: 1
kind: ArchivePolicy
defaults:
  timeout_seconds: 30
sources:
  - name: x
    source: /a
    repository: /b
"""
    with pytest.raises(PolicyError):
        load_policy_from_text(bad)


def test_defaults_constructor_round_trip() -> None:
    d = ArchiveDefaults()
    assert d.restic_bin == "restic"
    assert d.timeout_seconds == 7200


def test_source_requires_name_source_repository() -> None:
    with pytest.raises(Exception):
        ArchiveSource()  # type: ignore[call-arg]


def test_kind_discriminator_required() -> None:
    bad = """\
version: 1
sources:
  - name: x
    source: /a
    repository: /b
"""
    with pytest.raises(PolicyError):
        load_policy_from_text(bad)
