# SPDX-License-Identifier: Apache-2.0

"""Resolve the canonical inventory.db path from the operator environment.

``STEWARD_DATA_DIR`` takes priority, then this host's ``data_dir`` in the
estate file, then the XDG-style ``platformdirs`` default. The path is
*resolved* (not created) — callers that need the file decide whether to
``mkdir -p`` the parent, after :func:`assert_data_dir_mounted`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from platformdirs import user_data_path

from steward.core.errors import DataDirUnavailableError
from steward.infra.estate.host import resolve_host
from steward.infra.estate.loader import current

DataDirSource = Literal["STEWARD_DATA_DIR", "estate", "default"]


def resolve_data_dir() -> tuple[Path, DataDirSource]:
    """The data dir and which rule chose it.

    On macOS the default is ``~/Library/Application Support/steward``; on
    Linux it's ``$XDG_DATA_HOME/steward`` (defaulting to
    ``~/.local/share/steward``). The plan calls out
    ``~/.local/share/steward`` explicitly; users who want that on macOS
    set ``STEWARD_DATA_DIR=$HOME/.local/share/steward`` in their shell.
    """
    override = os.getenv("STEWARD_DATA_DIR")
    if override:
        return Path(override).expanduser(), "STEWARD_DATA_DIR"
    estate = current()
    if estate is not None:
        host_id = resolve_host(estate).host_id
        configured = estate.hosts[host_id].data_dir if host_id is not None else None
        if configured:
            return Path(configured), "estate"
    return user_data_path("steward", appauthor=False), "default"


def data_dir() -> Path:
    """``$STEWARD_DATA_DIR``, else the estate's ``data_dir`` for this host, else the platformdirs default."""
    return resolve_data_dir()[0]


def assert_data_dir_mounted(path: str | Path) -> None:
    """Refuse a path under ``/Volumes/<name>`` while that volume is not mounted.

    An unmounted volume leaves an ordinary directory (or nothing) at its
    mount point; creating the data dir there would start a fresh
    inventory.db with a new machine_id on the boot disk.
    """
    real = Path(os.path.realpath(Path(path).expanduser()))
    parts = real.parts
    if len(parts) < 3 or parts[:2] != ("/", "Volumes"):
        return
    mount_point = Path("/Volumes", parts[2])
    if not os.path.ismount(mount_point):
        raise DataDirUnavailableError(
            f"{mount_point} is not mounted; refusing to create or open {path} on the boot disk. "
            "Mount the volume, or point STEWARD_DATA_DIR / STEWARD_DB_PATH elsewhere."
        )


def inventory_db_path() -> Path:
    """Return the canonical ``inventory.db`` path.

    Override chain (first match wins):

    1. ``STEWARD_DB_PATH`` — explicit file override, useful in tests.
    2. ``STEWARD_DATA_DIR`` — override the parent directory, file name
       stays ``inventory.db``.
    3. ``hosts.<this host>.data_dir`` from the estate file.
    4. ``platformdirs.user_data_path("steward")`` — XDG default.
    """
    db_override = os.getenv("STEWARD_DB_PATH")
    if db_override:
        return Path(db_override).expanduser()
    return data_dir() / "inventory.db"


def imports_dir() -> Path:
    """Return the directory under which imported inventories live (ADR-0013).

    Each imported snapshot lands at
    ``<imports_dir>/<exporter_machine_id>/<iso8601>.db``. The parent
    is the canonical data dir — colocated with ``inventory.db`` so a
    single backup target captures everything.
    """
    return data_dir() / "imports"
