# SPDX-License-Identifier: Apache-2.0

"""Logging filter that attaches a run-id to every LogRecord.

Lifted shape from Cerid's ``app.observability.request_id_filter`` but
adapted for Steward's lifecycle: CLI invocations don't have request IDs,
they have *run IDs* (``scan_run_id``, ``manifest_run_id``). The contextvar
is owned here in v0.1; the v0.2 daemon can replace ``_run_id_var`` with a
``core.utils.tracing.request_id_var`` once the daemon is in.

Format string example::

    "%(asctime)s - %(name)s - %(run_id)s - %(levelname)s - %(message)s"

Emits ``"-"`` outside an active run (CLI bootstrap, alembic env, etc.) so
the format placeholder always resolves.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar

_run_id_var: ContextVar[str] = ContextVar("steward_run_id", default="")


def get_run_id() -> str:
    """Return the active run-id, or ``""`` when none is set."""
    return _run_id_var.get()


def set_run_id(run_id: str) -> None:
    """Set the active run-id for the current async/thread context."""
    _run_id_var.set(run_id)


class RunIdFilter(logging.Filter):
    """Inject the active contextvar run-id into each LogRecord.

    Pairs with a format string referencing ``%(run_id)s``. Safe to install
    on every handler — idempotent and cheap.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = get_run_id() or "-"
        return True
