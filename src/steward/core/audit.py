# SPDX-License-Identifier: Apache-2.0

"""Audit-log hash-chain helpers.

The audit_log table is append-only (enforced by SQLite ``BEFORE UPDATE``
and ``BEFORE DELETE`` triggers in the 0001 migration). Tamper evidence
comes from chaining: every row's ``row_hash`` covers the previous row's
``row_hash`` plus a canonical serialisation of this row's payload. Mutate
one row and every subsequent ``row_hash`` becomes inconsistent.

Verification (``steward db verify``) walks ``ORDER BY id`` and recomputes
each ``row_hash`` from the stored fields. The chain is broken iff any
recomputed hash differs from the stored hash.

The genesis row uses ``GENESIS_PREV_HASH`` as ``prev_hash``.
"""
from __future__ import annotations

import json
from typing import Any

import blake3 as _blake3

GENESIS_PREV_HASH = "0" * 64
"""Synthetic prev_hash for the first audit row (and the empty-table state).

Choosing all-zeros (rather than ``""`` or ``None``) lets the chain check
treat the empty table as ``prev_hash = GENESIS`` without a special case.
"""


def canonical_payload(row: dict[str, Any]) -> bytes:
    """Serialise an audit-row payload deterministically.

    JSON with ``sort_keys=True`` + ``separators=(",", ":")`` yields a
    byte-stable representation across Python versions and platforms.
    Excluded keys: ``id``, ``prev_hash``, ``row_hash`` (id is assigned
    by SQLite at insert; the two hashes are derived).
    """
    excluded = {"id", "prev_hash", "row_hash"}
    minimal = {k: v for k, v in row.items() if k not in excluded}
    return json.dumps(minimal, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def compute_row_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    """Return ``blake3(prev_hash || canonical_payload(payload))`` as hex.

    ``prev_hash`` is read by the chain — pass the prior row's ``row_hash``,
    or :data:`GENESIS_PREV_HASH` if writing the first row.
    """
    if len(prev_hash) != 64:
        raise ValueError(f"prev_hash must be 64 hex chars, got {len(prev_hash)}")
    h = _blake3.blake3()
    h.update(prev_hash.encode("ascii"))
    h.update(canonical_payload(payload))
    return h.hexdigest()
