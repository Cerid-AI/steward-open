# SPDX-License-Identifier: Apache-2.0

"""Alembic runtime env for Steward.

Steward stores its DDL as raw-SQL revisions (no SQLAlchemy declarative
metadata). Online migrations open a sqlite3 connection via Steward's
:func:`steward.infra.db.connect.connect` helper so WAL + sqlite_vec are
loaded the same way runtime queries see them.
"""

from __future__ import annotations

import logging
import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

from steward.infra.db.connect import connect
from steward.infra.db.settings import inventory_db_path

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger("steward.alembic")


def _target_db_path() -> Path:
    """Override the alembic-ini SQLAlchemy URL with our resolved path."""
    override = os.getenv("STEWARD_DB_PATH")
    if override:
        return Path(override).expanduser()
    return inventory_db_path()


def run_migrations_offline() -> None:
    """Offline mode is intentionally unsupported.

    Steward migrations issue raw SQLite DDL that depends on triggers and
    pragmas. Offline mode would emit a script disconnected from the live
    pragma state, which is misleading. Run migrations online or fail.
    """
    raise RuntimeError(
        "Steward alembic env does not support offline migrations. "
        "Run 'steward db migrate' (online mode), or invoke alembic without --sql."
    )


def run_migrations_online() -> None:
    """Online migration — opens the canonical Steward connection.

    Alembic needs a SQLAlchemy connection (for dialect discovery), but the
    real connection underneath must carry Steward's WAL + sqlite_vec
    pragmas. We give SQLAlchemy a ``creator`` that returns a connection
    pre-configured by :func:`steward.infra.db.connect.connect`.
    """
    db_path = _target_db_path()
    logger.info("Running migrations against %s", db_path)
    engine = create_engine(
        "sqlite://",
        creator=lambda: connect(db_path, load_vec=True),
        future=True,
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
