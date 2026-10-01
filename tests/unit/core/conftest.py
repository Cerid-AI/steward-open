# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for core unit tests."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from steward.core.estate import Estate

TWO_HOST_ESTATE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "estate" / "two-host.yml"
TWO_HOST_ESTATE_YAML = TWO_HOST_ESTATE_PATH.read_text(encoding="utf-8")


@pytest.fixture
def two_host_raw() -> dict[str, Any]:
    """The two-host estate as a mutable mapping, for validator tests."""
    return copy.deepcopy(yaml.safe_load(TWO_HOST_ESTATE_YAML))


@pytest.fixture
def two_host_estate(two_host_raw: dict[str, Any]) -> Estate:
    return Estate.model_validate(two_host_raw)
