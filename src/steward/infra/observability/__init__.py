"""Steward observability — Sentry init, swallowed-error helper, request-id filter.

The Sentry helper is a no-op when ``SENTRY_DSN`` (or ``SENTRY_DSN_STEWARD``)
is unset, preserving the privacy-first default for local-only operators.
"""

from steward.infra.observability.sentry_init import init_sentry
from steward.infra.observability.swallowed import log_swallowed_error

__all__ = ["init_sentry", "log_swallowed_error"]
