# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the launchctl subprocess wrapper.

These tests don't invoke ``launchctl``. They cover:

1. `launchctl_available` reflects `shutil.which` behaviour.
2. `bootstrap_plist` / `bootout_plist` / `print_service_status`
   raise `LaunchctlNotInstalledError` when the binary is missing.
3. Argv construction uses the right `gui/<uid>` domain target.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from steward.infra.schedule import launchctl as launchctl_mod
from steward.infra.schedule.launchctl import (
    LaunchctlNotInstalledError,
    bootout_plist,
    bootstrap_plist,
    launchctl_available,
    print_service_status,
)

# ─────────────────────── availability ──────────────────────────


def test_available_reflects_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        launchctl_mod, "shutil", _FakeShutil(found=True)
    )
    assert launchctl_available() is True
    monkeypatch.setattr(
        launchctl_mod, "shutil", _FakeShutil(found=False)
    )
    assert launchctl_available() is False


class _FakeShutil:
    def __init__(self, *, found: bool) -> None:
        self._found = found

    def which(self, name: str) -> str | None:
        del name
        return "/usr/bin/launchctl" if self._found else None


# ─────────────────────── missing-binary error path ──────────────────────────


def test_bootstrap_raises_when_launchctl_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launchctl_mod, "shutil", _FakeShutil(found=False))
    with pytest.raises(LaunchctlNotInstalledError):
        bootstrap_plist(Path("/x.plist"))


def test_bootout_raises_when_launchctl_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launchctl_mod, "shutil", _FakeShutil(found=False))
    with pytest.raises(LaunchctlNotInstalledError):
        bootout_plist("com.example.foo")


def test_print_status_raises_when_launchctl_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launchctl_mod, "shutil", _FakeShutil(found=False))
    with pytest.raises(LaunchctlNotInstalledError):
        print_service_status("com.example.foo")


# ─────────────────────── argv construction ──────────────────────────


def test_bootstrap_argv_uses_gui_domain_and_plist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When launchctl IS available, the wrapper builds the right argv.

    We stub the actual `subprocess.run` so the test runs cross-platform
    (Linux CI has no launchctl) — we only care about the argv shape.
    """
    monkeypatch.setattr(launchctl_mod, "shutil", _FakeShutil(found=True))

    captured: dict[str, object] = {}

    class _FakeCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(argv: list[str], **kwargs: object) -> _FakeCompletedProcess:
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return _FakeCompletedProcess()

    monkeypatch.setattr(launchctl_mod.subprocess, "run", _fake_run)

    result = bootstrap_plist(Path("/tmp/x.plist"), uid=501)
    assert result.returncode == 0
    argv = captured["argv"]
    assert argv[:2] == ["launchctl", "bootstrap"]
    assert argv[2] == "gui/501"
    assert argv[3] == "/tmp/x.plist"


def test_bootout_argv_targets_label_under_gui_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launchctl_mod, "shutil", _FakeShutil(found=True))

    captured: dict[str, object] = {}

    class _FakeCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(argv: list[str], **kwargs: object) -> _FakeCompletedProcess:
        captured["argv"] = list(argv)
        return _FakeCompletedProcess()

    monkeypatch.setattr(launchctl_mod.subprocess, "run", _fake_run)

    bootout_plist("com.cerid.steward.weekly-verify", uid=501)
    argv = captured["argv"]
    assert argv == [
        "launchctl",
        "bootout",
        "gui/501/com.cerid.steward.weekly-verify",
    ]


def test_print_argv_targets_label_under_gui_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launchctl_mod, "shutil", _FakeShutil(found=True))

    captured: dict[str, object] = {}

    class _FakeCompletedProcess:
        returncode = 0
        stdout = "service info"
        stderr = ""

    def _fake_run(argv: list[str], **kwargs: object) -> _FakeCompletedProcess:
        captured["argv"] = list(argv)
        return _FakeCompletedProcess()

    monkeypatch.setattr(launchctl_mod.subprocess, "run", _fake_run)

    result = print_service_status("com.cerid.steward.x", uid=999)
    assert result.stdout == "service info"
    assert captured["argv"] == [
        "launchctl",
        "print",
        "gui/999/com.cerid.steward.x",
    ]
