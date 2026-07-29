# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the rclone subprocess wrapper.

These tests don't actually invoke ``rclone`` — they exercise the
command-builder + the JSON-log stats parser. The runner-level tests
in ``tests/integration/test_replicate_runner.py`` cover the audit
chain + skip-on-missing-tool behaviour.
"""

from __future__ import annotations

from steward.core.policy.schema import ReplicationDefaults, ReplicationSource
from steward.infra.replicate.rclone import _build_argv, _parse_stats


def test_argv_uses_copy_by_default() -> None:
    defaults = ReplicationDefaults()
    src = ReplicationSource(
        name="x",
        source="/a",
        destination="/b",
    )
    argv = _build_argv(defaults=defaults, source=src, dry_run=False)
    assert argv[:4] == ["rclone", "copy", "/a", "/b"]
    assert "--use-json-log" in argv
    assert "--transfers" in argv
    assert "4" in argv  # default
    assert "--dry-run" not in argv


def test_argv_dry_run_flag_passed_through() -> None:
    defaults = ReplicationDefaults()
    src = ReplicationSource(name="x", source="/a", destination="/b")
    argv = _build_argv(defaults=defaults, source=src, dry_run=True)
    assert "--dry-run" in argv


def test_argv_sync_mode_replaces_copy() -> None:
    defaults = ReplicationDefaults()
    src = ReplicationSource(name="x", source="/a", destination="/b", mode="sync")
    argv = _build_argv(defaults=defaults, source=src, dry_run=False)
    assert argv[1] == "sync"


def test_argv_includes_then_excludes_in_rclone_order() -> None:
    """rclone evaluates filter rules in argv order — includes-first lets
    a small whitelist work even with broad excludes after."""
    defaults = ReplicationDefaults()
    src = ReplicationSource(
        name="x",
        source="/a",
        destination="/b",
        includes=["*.db"],
        excludes=["*.tmp"],
    )
    argv = _build_argv(defaults=defaults, source=src, dry_run=False)
    inc_idx = argv.index("--include")
    exc_idx = argv.index("--exclude")
    assert inc_idx < exc_idx


def test_argv_appends_extra_args() -> None:
    defaults = ReplicationDefaults(extra_args=["--bwlimit", "10M"])
    src = ReplicationSource(name="x", source="/a", destination="/b")
    argv = _build_argv(defaults=defaults, source=src, dry_run=False)
    assert argv[-2:] == ["--bwlimit", "10M"]


def test_parse_stats_extracts_last_summary() -> None:
    """The parser finds the last JSON log line that has a 'stats' key."""
    stderr = (
        '{"level":"info","msg":"started"}\n'
        '{"level":"notice","msg":"progress","stats":{"bytes":100,"transfers":1,"errors":0}}\n'
        '{"level":"notice","msg":"final","stats":{"bytes":2048,"transfers":3,"errors":0,"elapsedTime":1.5}}\n'
    )
    stats = _parse_stats(stderr)
    assert stats["bytes"] == 2048
    assert stats["transfers"] == 3
    assert stats["elapsedTime"] == 1.5


def test_parse_stats_returns_empty_on_no_json() -> None:
    """When rclone emits no parseable JSON, parser returns an empty dict
    (not None) so callers can unconditionally .get()."""
    assert _parse_stats("rclone: command not found\n") == {}
    assert _parse_stats("") == {}


def test_parse_stats_ignores_non_numeric_fields() -> None:
    """rclone occasionally embeds arrays (errors_list); the parser drops
    non-numeric fields so the stats dict is JSON-payload-safe."""
    stderr = '{"stats":{"bytes":100,"errors_list":["a","b"],"transfers":1}}\n'
    stats = _parse_stats(stderr)
    assert stats == {"bytes": 100, "transfers": 1}
