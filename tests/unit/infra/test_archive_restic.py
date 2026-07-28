# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the restic subprocess wrapper.

Restic isn't required by tests — we exercise the argv builders, env
setup, and JSON-output parsers directly. The runner-level tests in
``tests/integration/test_archive_runner.py`` cover the audit chain.
"""
from __future__ import annotations

from steward.core.policy.schema import ArchiveDefaults, ArchiveSource
from steward.infra.archive.restic import (
    _build_env,
    _parse_backup_summary,
    _parse_init_summary,
    _parse_snapshots_list,
)

# ─────────────────────── env construction ──────────────────────────


def test_env_carries_password_command() -> None:
    d = ArchiveDefaults(password_command="security find …")  # pragma: allowlist secret
    env = _build_env(d)
    assert env["RESTIC_PASSWORD_COMMAND"] == "security find …"  # pragma: allowlist secret
    assert "RESTIC_PASSWORD_FILE" not in env


def test_env_carries_password_file() -> None:
    d = ArchiveDefaults(password_file="/path/to/file")
    env = _build_env(d)
    assert env["RESTIC_PASSWORD_FILE"] == "/path/to/file"
    assert "RESTIC_PASSWORD_COMMAND" not in env


def test_env_passes_through_parent_when_no_password_set() -> None:
    """Neither password var is set, but other parent-env vars carry through."""
    import os

    os.environ["STEWARD_TEST_SENTINEL"] = "yes"
    try:
        env = _build_env(ArchiveDefaults())
        assert env.get("STEWARD_TEST_SENTINEL") == "yes"
        assert "RESTIC_PASSWORD_COMMAND" not in env
        assert "RESTIC_PASSWORD_FILE" not in env
    finally:
        os.environ.pop("STEWARD_TEST_SENTINEL", None)


def test_archive_source_can_be_used_with_defaults() -> None:
    """Just a smoke that the policy types compose. (Argv builders run
    inside ``run_restic_*`` which require restic on PATH; the runner
    tests cover them via fakes.)
    """
    src = ArchiveSource(
        name="x", source="/a", repository="/b", tags=["t"]
    )
    assert src.exclude_caches is True
    assert src.tags == ["t"]


# ─────────────────────── backup summary parser ──────────────────────────


def test_backup_summary_extracts_last_summary_message() -> None:
    """``restic backup --json`` emits one JSON object per line. The
    ``message_type=summary`` row is the last one; the parser picks it."""
    stdout = (
        '{"message_type":"status","percent_done":0.5}\n'
        '{"message_type":"summary","files_new":3,"data_added":1024,'
        '"snapshot_id":"abc123","total_bytes_processed":2048}\n'
    )
    summary = _parse_backup_summary(stdout)
    assert summary["files_new"] == 3
    assert summary["snapshot_id"] == "abc123"
    assert summary["data_added"] == 1024


def test_backup_summary_returns_empty_when_no_summary_line() -> None:
    stdout = (
        '{"message_type":"status","percent_done":0.5}\n'
        '{"message_type":"error","error":"boom"}\n'
    )
    assert _parse_backup_summary(stdout) == {}


def test_backup_summary_skips_non_json_lines() -> None:
    stdout = (
        "loading config\n"
        '{"message_type":"summary","snapshot_id":"x"}\n'
        "done\n"
    )
    assert _parse_backup_summary(stdout) == {
        "message_type": "summary",
        "snapshot_id": "x",
    }


# ─────────────────────── snapshots list parser ──────────────────────────


def test_snapshots_list_parses_json_array() -> None:
    stdout = (
        '[{"short_id":"abc","time":"2026-01-01T00:00:00Z","paths":["/a"]},'
        '{"short_id":"def","time":"2026-01-02T00:00:00Z","paths":["/b"]}]'
    )
    parsed = _parse_snapshots_list(stdout)
    assert len(parsed) == 2
    assert parsed[0]["short_id"] == "abc"
    assert parsed[1]["paths"] == ["/b"]


def test_snapshots_list_returns_empty_on_empty_stdout() -> None:
    assert _parse_snapshots_list("") == []
    assert _parse_snapshots_list("   ") == []


def test_snapshots_list_returns_empty_on_non_array_output() -> None:
    # restic typically prints a JSON object on error; the parser
    # gracefully returns an empty list.
    assert _parse_snapshots_list('{"error":"unreadable"}') == []
    assert _parse_snapshots_list("not even json") == []


def test_snapshots_list_drops_non_dict_entries() -> None:
    """A pathological array containing non-object entries shouldn't
    propagate them into the parsed result."""
    assert _parse_snapshots_list('[{"ok":true}, "string", 42]') == [
        {"ok": True}
    ]


# ─────────────────────── init summary parser ──────────────────────────


def test_init_summary_parses_json_object() -> None:
    parsed = _parse_init_summary('{"repository":"/b","encryption":"true"}')
    assert parsed["repository"] == "/b"


def test_init_summary_returns_empty_on_non_object() -> None:
    assert _parse_init_summary("") == {}
    assert _parse_init_summary("[1,2]") == {}
    assert _parse_init_summary("not json") == {}
