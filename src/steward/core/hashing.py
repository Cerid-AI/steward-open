# SPDX-License-Identifier: Apache-2.0

"""Two-grade content hashing — fast xxh3-128 + archive blake3.

ADR-0005 fixes the policy: every scanned file gets xxh3-128 in the fast
pass. Steward promotes to blake3 (archive grade) when *either*:

* the size threshold is exceeded (default 100 MiB — long-tail content is
  worth the extra cost up front), or
* the fast hash collides with another already-known permanode (the
  reconciler asks for promotion before merging the permanodes).

sha256 is preserved on legacy claims as a compatibility column.

Both hashes are computed over the raw file bytes; no path / metadata
hashing. Streaming chunks let us hash files larger than RAM.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import blake3 as _blake3
import xxhash

_DEFAULT_CHUNK = 8 * 1024 * 1024  # 8 MiB — matches sprawl-audit walker
DEFAULT_BLAKE3_PROMOTE_BYTES = 100 * 1024 * 1024  # 100 MiB


def new_hasher_for(algo: str) -> object:
    """Return a fresh ``hashlib``-style hasher for ``algo``.

    Single-pass copy-and-hash code (e.g. ``promote_with_verify``)
    needs to incrementally feed file chunks to a hasher matching
    the permanode's algo. This helper returns the right instance.

    Falls back to blake3 for unrecognised algos.
    """
    if algo == "xxh3_128":
        return xxhash.xxh3_128()
    if algo == "sha256":
        return hashlib.sha256()
    return _blake3.blake3()


def hash_file_by_algo(
    path: Path | str,
    *,
    algo: str,
    chunk_size: int = _DEFAULT_CHUNK,
) -> tuple[str, int]:
    """Stream ``path`` once and return ``(hex_digest, size_bytes)`` under ``algo``.

    Supports the algorithms that appear in Steward's
    ``permanodes.canonical_hash_algo`` column:

    * ``blake3`` (archive grade, hash ladder)
    * ``xxh3_128`` (fast pass, small files)
    * ``sha256`` (legacy — permanodes imported from
      sprawl-audit's ``unified-hash.db``)

    Falls back to blake3 for unrecognised algos.

    Single canonical helper for every Steward verify path that
    needs to re-hash a file against its inventory-recorded hash —
    promote_with_verify, retire_direct, stash verify, etc. Keeps
    them all consistent when new algos enter the catalogue.
    """
    target = Path(path)
    size = 0
    if algo == "xxh3_128":
        x = xxhash.xxh3_128()
        with open(target, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                x.update(chunk)
                size += len(chunk)
        return (x.hexdigest(), size)
    if algo == "sha256":
        sh = hashlib.sha256()
        with open(target, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                sh.update(chunk)
                size += len(chunk)
        return (sh.hexdigest(), size)
    # default = blake3
    h = _blake3.blake3()
    with open(target, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return (h.hexdigest(), size)


@dataclass(frozen=True)
class HashResult:
    algo: str
    hex: str
    size_bytes: int


class HashLadder:
    """Compute xxh3-128 (fast) and blake3 (archive) over a file.

    The class is stateless past construction — every call streams the
    file. Callers cache the result against the path / mtime if they want
    to skip the re-read.
    """

    def __init__(
        self,
        *,
        chunk_size: int = _DEFAULT_CHUNK,
        promote_threshold_bytes: int = DEFAULT_BLAKE3_PROMOTE_BYTES,
    ) -> None:
        self.chunk_size = chunk_size
        self.promote_threshold_bytes = promote_threshold_bytes

    def fast(self, path: Path | str) -> HashResult:
        """Stream the file once with xxh3-128 (very fast, content addressed)."""
        h = xxhash.xxh3_128()
        size = 0
        with open(path, "rb") as f:
            while True:
                chunk = f.read(self.chunk_size)
                if not chunk:
                    break
                h.update(chunk)
                size += len(chunk)
        return HashResult(algo="xxh3_128", hex=h.hexdigest(), size_bytes=size)

    def archive(self, path: Path | str) -> HashResult:
        """Stream the file once with blake3 (archive-grade content hash)."""
        h = _blake3.blake3()
        size = 0
        with open(path, "rb") as f:
            while True:
                chunk = f.read(self.chunk_size)
                if not chunk:
                    break
                h.update(chunk)
                size += len(chunk)
        return HashResult(algo="blake3", hex=h.hexdigest(), size_bytes=size)

    def should_promote(self, *, size_bytes: int, suspected_dup: bool = False) -> bool:
        """Decide whether to run blake3 in addition to xxh3.

        Returns True when either:

        * ``size_bytes >= promote_threshold_bytes`` — the file is large
          enough that the extra hash cost is amortised; or
        * ``suspected_dup=True`` — caller already has a permanode with the
          matching xxh3 and wants the blake3 confirmation before merging.
        """
        return size_bytes >= self.promote_threshold_bytes or suspected_dup
