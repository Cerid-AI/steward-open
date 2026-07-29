# SPDX-License-Identifier: Apache-2.0

"""Deterministic identifier helpers.

A *permanode id* is the deduplication identity. It is derived from the
content's canonical hash and size — same bytes anywhere, anytime, on any
machine produces the same permanode id. Format:

    permanode_id = blake3(canonical_hash || ":" || size_bytes)[:32]

The 32-hex prefix is enough collision resistance for the realistic
inventory size (≈10^7 unique permanodes ⇒ ≈10^-21 collision probability
under the birthday bound) while staying short enough to read at a glance
in `steward inspect` output.

The colon delimiter prevents the ``("xyz", 12)`` and ``("xyz12", "")``
collision where two distinct ``canonical_hash || size_bytes`` strings
collapse to the same byte sequence.
"""

from __future__ import annotations

import blake3 as _blake3


def permanode_id(canonical_hash: str, size_bytes: int) -> str:
    """Return the deterministic permanode id for ``(canonical_hash, size_bytes)``.

    Parameters
    ----------
    canonical_hash
        Hex string of the canonical content hash (blake3 by default; sha256
        accepted during legacy import). Case-folded to lower; whitespace
        stripped. Empty string raises ``ValueError``.
    size_bytes
        Non-negative integer. Negative values raise ``ValueError``.
    """
    if not canonical_hash:
        raise ValueError("canonical_hash must be a non-empty hex string")
    if size_bytes < 0:
        raise ValueError(f"size_bytes must be non-negative, got {size_bytes}")
    norm = canonical_hash.strip().lower()
    payload = f"{norm}:{size_bytes}".encode("ascii")
    digest = _blake3.blake3(payload).hexdigest()
    return digest[:32]
