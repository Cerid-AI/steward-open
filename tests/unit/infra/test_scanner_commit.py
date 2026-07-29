# SPDX-License-Identifier: Apache-2.0

"""Mid-walk scan commit cadence."""

from __future__ import annotations

from steward.infra.scanner.walker import _commit_every


def test_commit_every_default(monkeypatch) -> None:
    monkeypatch.delenv("STEWARD_SCAN_COMMIT_EVERY", raising=False)
    assert _commit_every() == 250


def test_commit_every_override(monkeypatch) -> None:
    monkeypatch.setenv("STEWARD_SCAN_COMMIT_EVERY", "100")
    assert _commit_every() == 100


def test_commit_every_disable(monkeypatch) -> None:
    monkeypatch.setenv("STEWARD_SCAN_COMMIT_EVERY", "0")
    assert _commit_every() == 0


def test_commit_every_invalid(monkeypatch) -> None:
    monkeypatch.setenv("STEWARD_SCAN_COMMIT_EVERY", "nope")
    assert _commit_every() == 250
