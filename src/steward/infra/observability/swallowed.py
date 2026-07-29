# SPDX-License-Identifier: Apache-2.0

"""Observable counterpart to ``except Exception: pass``.

Adapted from Cerid's ``core.utils.swallowed`` (FE/Python parity sweep). The
Cerid original tracked a Redis sorted-set counter per module for dashboard
surfacing; Steward is a single-process CLI with no Redis dependency, so the
counter path is dropped here. The contract that matters — every broad
``except`` documents *why* and increments observability — is preserved via
a structlog warning + Sentry breadcrumb.

The lint rule in ``scripts/lint-no-silent-catch.py`` blocks new ``except
Exception: pass`` forms unless they call ``log_swallowed_error(...)`` on
the same logical site.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("steward.swallowed")


def log_swallowed_error(
    module: str,
    exc: BaseException,
    *,
    context: dict[str, Any] | None = None,
) -> None:
    """Record a swallowed exception.

    Always logs at WARNING with module + exc type. When ``sentry_sdk`` is
    installed (it is, as a Steward runtime dep), adds a breadcrumb so the
    swallow is visible in the next genuine error event. The swallow is
    deliberate; we never ``capture_exception`` for swallowed errors.

    Parameters
    ----------
    module
        Logical subsystem swallowing the error. Stable string — do NOT use
        ``__name__`` because it changes when modules move. Examples:
        ``"scanner.walker"``, ``"importer.legacy_unified"``,
        ``"stash.same_fs_rename"``.
    exc
        The exception instance. Only type + str(exc) are logged; the
        traceback is intentionally NOT captured (the swallow is by design).
    context
        Optional structured metadata for log correlation.
    """
    logger.warning(
        "swallowed %s in %s: %s",
        type(exc).__name__,
        module,
        exc,
        extra={"swallowed_module": module, **(context or {})},
    )
    try:
        import sentry_sdk

        sentry_sdk.add_breadcrumb(
            category="swallowed",
            message=f"{type(exc).__name__} in {module}: {exc}",
            level="warning",
            data=context or {},
        )
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 — observability must never itself raise
        pass
