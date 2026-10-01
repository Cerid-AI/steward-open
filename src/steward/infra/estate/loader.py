# SPDX-License-Identifier: Apache-2.0

"""Locate, parse and cache the operator's estate file.

``$STEWARD_ESTATE_CONFIG`` names the file explicitly; otherwise
``~/.config/steward/estate.yml`` is used if it exists. No file means no
estate: callers fall back to the single-host legacy behaviour.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from steward.core.errors import EstateError
from steward.core.estate import Estate

ESTATE_CONFIG_ENV = "STEWARD_ESTATE_CONFIG"


def default_estate_path() -> Path:
    return Path.home() / ".config" / "steward" / "estate.yml"


def estate_config_path() -> Path:
    """The estate file Steward reads (whether or not it exists)."""
    override = os.getenv(ESTATE_CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    return default_estate_path()


def load_estate_text(text: str, source: str = "<text>") -> Estate:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise EstateError(f"{source}: YAML parse error: {exc}") from exc
    if not isinstance(data, dict):
        raise EstateError(f"{source}: estate root must be a mapping, got {type(data).__name__}")
    try:
        return Estate.model_validate(data)
    except ValidationError as exc:
        raise EstateError(f"{source}: invalid estate: {exc}") from exc


def load_estate(path: Path) -> Estate:
    """Parse + validate the estate at ``path``; raises :class:`EstateError`."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EstateError(f"estate file not found: {path}") from exc
    except OSError as exc:
        raise EstateError(f"{path}: cannot read estate file: {exc}") from exc
    return load_estate_text(text, str(path))


def find_estate() -> Estate | None:
    """Load the configured estate, uncached.

    A missing default file means legacy mode (``None``). A path named by
    ``$STEWARD_ESTATE_CONFIG`` must exist: a typo there would otherwise
    silently drop the host back to legacy data-dir and ownership rules.
    """
    path = estate_config_path()
    if not os.getenv(ESTATE_CONFIG_ENV) and not path.exists():
        return None
    return load_estate(path)


_cache: tuple[Path, Estate | None] | None = None


def current() -> Estate | None:
    """The configured estate, loaded once per config path for the process."""
    global _cache
    path = estate_config_path()
    if _cache is None or _cache[0] != path:
        _cache = (path, find_estate())
    return _cache[1]


def reset_cache() -> None:
    """Forget the cached estate (tests, or after the file changes)."""
    global _cache
    _cache = None
