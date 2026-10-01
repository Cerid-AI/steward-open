# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the df-vs-reachable gap check.

The incident this automates: 743 GiB allocated, 293 GiB reachable, and the
441 GiB difference sitting in a DataVault for 46 days while every directory
listing looked innocent. The evaluator tests plant that exact shape.
"""

from __future__ import annotations

import subprocess

from steward.core.health import (
    DEFAULT_THRESHOLDS,
    UnaccountedSpace,
    check_unaccounted_space,
    unaccounted_level,
)
from steward.infra.health import unaccounted as un

GIB = 1024**3


class TestLevels:
    def test_incident_gap_fails(self) -> None:
        """441 GiB unreachable is the real 2026-08-16 reading."""
        assert unaccounted_level(441 * GIB, thresholds=DEFAULT_THRESHOLDS) == "fail"

    def test_normal_vault_baseline_is_ok(self) -> None:
        """DataVaults and fs metadata make tens of GiB NORMAL on macOS."""
        assert unaccounted_level(30 * GIB, thresholds=DEFAULT_THRESHOLDS) == "ok"

    def test_warn_band(self) -> None:
        assert unaccounted_level(80 * GIB, thresholds=DEFAULT_THRESHOLDS) == "warn"

    def test_unmeasured_is_unknown(self) -> None:
        assert unaccounted_level(None, thresholds=DEFAULT_THRESHOLDS) == "unknown"

    def test_floors_are_ordered(self) -> None:
        assert (
            DEFAULT_THRESHOLDS.unaccounted_warn_bytes
            < DEFAULT_THRESHOLDS.unaccounted_fail_bytes
        )


class TestCheck:
    def test_not_run_is_skipped_never_ok(self) -> None:
        """What makes unaccounted_space safe in the DEFAULT fail-on set."""
        assert check_unaccounted_space(None).level == "skipped"

    def test_failed_measurement_is_unknown(self) -> None:
        section = UnaccountedSpace(root="/x", error="du timed out", level="unknown")
        assert check_unaccounted_space(section).level == "unknown"

    def test_incident_shape_fails(self) -> None:
        section = UnaccountedSpace(
            root="/System/Volumes/Data",
            used_bytes=743 * GIB,
            reachable_bytes=293 * GIB,
            gap_bytes=450 * GIB,
        )
        result = check_unaccounted_space(section)
        assert result.level == "fail"
        assert "unreachable by traversal" in result.message


class TestCollector:
    def test_du_nonzero_exit_still_yields_a_total(self, monkeypatch, tmp_path) -> None:
        """du exits non-zero whenever ANY subtree was unreadable — which on a
        volume with DataVaults is every run, and is exactly the condition the
        check quantifies. The stdout total must be used regardless."""
        def fake_run(*_a, **_k):
            return subprocess.CompletedProcess(
                args=[], returncode=1,
                stdout="1048576\t/x\n", stderr="du: /x/vault: Operation not permitted\n",
            )
        monkeypatch.setattr(un.subprocess, "run", fake_run)
        section = un.collect_unaccounted(tmp_path)
        assert section.reachable_bytes == 1048576 * 1024
        assert section.gap_bytes is not None

    def test_du_timeout_is_unknown_not_ok(self, monkeypatch, tmp_path) -> None:
        def fake_run(*_a, **_k):
            raise subprocess.TimeoutExpired(cmd="du", timeout=1)
        monkeypatch.setattr(un.subprocess, "run", fake_run)
        section = un.collect_unaccounted(tmp_path)
        assert section.level == "unknown"
        assert section.gap_bytes is None

    def test_no_total_is_unknown(self, monkeypatch, tmp_path) -> None:
        def fake_run(*_a, **_k):
            return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
        monkeypatch.setattr(un.subprocess, "run", fake_run)
        assert un.collect_unaccounted(tmp_path).level == "unknown"
