# SPDX-License-Identifier: Apache-2.0

"""Resolve the canonical inventory.db path from the operator environment.

XDG-style defaults are layered through ``platformdirs``; ``STEWARD_DATA_DIR``
takes priority. The path is *resolved* (not created) — callers that need
the file decide whether to ``mkdir -p`` the parent.
"""
from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_path


def data_dir() -> Path:
    """Return ``$STEWARD_DATA_DIR`` if set, else the platformdirs default.

    On macOS the default is ``~/Library/Application Support/steward``; on
    Linux it's ``$XDG_DATA_HOME/steward`` (defaulting to
    ``~/.local/share/steward``). The plan calls out
    ``~/.local/share/steward`` explicitly; users who want that on macOS
    set ``STEWARD_DATA_DIR=$HOME/.local/share/steward`` in their shell.
    """
    override = os.getenv("STEWARD_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return user_data_path("steward", appauthor=False)


def inventory_db_path() -> Path:
    """Return the canonical ``inventory.db`` path.

    Override chain (first match wins):

    1. ``STEWARD_DB_PATH`` — explicit file override, useful in tests.
    2. ``STEWARD_DATA_DIR`` — override the parent directory, file name
       stays ``inventory.db``.
    3. ``platformdirs.user_data_path("steward")`` — XDG default.
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
