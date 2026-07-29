# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the hash ladder."""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.core.hashing import HashLadder


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    p = tmp_path / "f.bin"
    p.write_bytes(b"steward" * 64)
    return p


def test_fast_returns_xxh3(sample_file: Path) -> None:
    r = HashLadder().fast(sample_file)
    assert r.algo == "xxh3_128"
    assert len(r.hex) == 32
    assert r.size_bytes == sample_file.stat().st_size


def test_archive_returns_blake3(sample_file: Path) -> None:
    r = HashLadder().archive(sample_file)
    assert r.algo == "blake3"
    assert len(r.hex) == 64  # blake3 default 32-byte digest = 64 hex
    assert r.size_bytes == sample_file.stat().st_size


def test_same_bytes_same_hash(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_bytes(b"same content")
    b.write_bytes(b"same content")
    ladder = HashLadder()
    assert ladder.fast(a).hex == ladder.fast(b).hex
    assert ladder.archive(a).hex == ladder.archive(b).hex


def test_should_promote_size_threshold() -> None:
    ladder = HashLadder(promote_threshold_bytes=1024)
    assert ladder.should_promote(size_bytes=2048) is True
    assert ladder.should_promote(size_bytes=512) is False


def test_should_promote_suspected_dup() -> None:
    ladder = HashLadder(promote_threshold_bytes=10**9)
    # Far under the threshold — promote anyway when caller flags a dup.
    assert ladder.should_promote(size_bytes=1, suspected_dup=True) is True
    assert ladder.should_promote(size_bytes=1) is False
