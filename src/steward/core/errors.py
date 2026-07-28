# SPDX-License-Identifier: Apache-2.0

"""Steward exception hierarchy.

Every Steward-raised error inherits from :class:`StewardError` so callers
can catch the family without catching unrelated ``Exception`` subclasses.
"""
from __future__ import annotations


class StewardError(Exception):
    """Base for every Steward-raised exception."""


class SchemaError(StewardError):
    """Schema or migration invariant violated."""


class AuditChainBroken(StewardError):
    """The audit-log hash chain is broken; the DB is no longer tamper-evident."""


class PolicyError(StewardError):
    """A policy YAML is invalid, ambiguous, or references unknown identifiers."""


class ManifestError(StewardError):
    """A plan manifest is malformed, mismatched, or stale."""


class FPUnavailableError(StewardError):
    """A cloud-File-Provider tier could not service a filesystem operation
    (e.g. the sync agent's delete propagation timed out — ``Errno 60``). The
    manifest and data are fine; the File Provider is congested or degraded.
    Retryable: defer the affected row and retry once the FP has settled.
    See ``docs/field-notes-2026-07-13-fp-cleanup.md`` (gap #2)."""


class ScanError(StewardError):
    """A scanner failed to walk or hash a path."""


class ImportError_(StewardError):
    """Legacy-database import failed (named with trailing underscore to avoid shadowing
    Python's built-in ``ImportError``)."""
