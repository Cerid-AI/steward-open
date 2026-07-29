# SPDX-License-Identifier: Apache-2.0

"""Preservation invariants — merge-gate for every apply-lifecycle PR.

Each test asserts a property that, if relaxed, would compromise the
operator-in-the-loop contract. Don't relax these without an ADR.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.infra.db import repo_audit
from steward.infra.db.apply import apply_manifest
from steward.infra.db.connect import connect

pytestmark = pytest.mark.preservation


def _src_size(env: dict[str, Path]) -> int:
    return env["source"].stat().st_size


def test_dry_run_zero_writes(preservation_env: dict[str, Path]) -> None:
    """--dry-run never modifies any source file."""
    before_mtime = preservation_env["source"].stat().st_mtime
    before_size = _src_size(preservation_env)
    result = apply_manifest(
        manifest_path=preservation_env["manifest"],
        machine_id="preservation",
        dry_run=True,
    )
    assert result.dry_run is True
    assert result.rows_applied == 1
    # Source unchanged.
    assert preservation_env["source"].stat().st_mtime == before_mtime
    assert preservation_env["source"].stat().st_size == before_size
    # Stash destination NOT created.
    assert not preservation_env["stash"].exists()


def test_apply_execute_moves_file_and_audits(preservation_env: dict[str, Path]) -> None:
    """--execute renames the file + appends stash_committed + apply_end audit rows."""
    result = apply_manifest(
        manifest_path=preservation_env["manifest"],
        machine_id="preservation",
        dry_run=False,
    )
    assert result.dry_run is False
    assert result.rows_applied == 1
    assert not preservation_env["source"].exists()
    assert preservation_env["stash"].exists()

    con = connect(preservation_env["db"], read_only=True, load_vec=False)
    try:
        actions = [row[0] for row in con.execute("SELECT action FROM audit_log ORDER BY id ASC")]
    finally:
        con.close()
    assert actions == ["apply_start", "stash_committed", "apply_end"]


def test_audit_chain_intact_after_apply(preservation_env: dict[str, Path]) -> None:
    apply_manifest(
        manifest_path=preservation_env["manifest"],
        machine_id="preservation",
        dry_run=False,
    )
    con = connect(preservation_env["db"], read_only=True, load_vec=False)
    try:
        ok, n, err = repo_audit.verify_chain(con)
    finally:
        con.close()
    assert ok, err
    assert n == 3  # apply_start, stash_committed, apply_end


def test_idempotent_re_apply_is_safe(preservation_env: dict[str, Path]) -> None:
    """A second apply against the same manifest after success fails the
    rows whose source has already moved — but the apply itself doesn't
    crash, and the audit chain stays intact."""
    apply_manifest(
        manifest_path=preservation_env["manifest"],
        machine_id="preservation",
        dry_run=False,
    )
    result = apply_manifest(
        manifest_path=preservation_env["manifest"],
        machine_id="preservation",
        dry_run=False,
    )
    # The source is gone now → ManifestError per row → rows_errored=1.
    assert result.rows_errored == 1
    assert result.rows_applied == 0

    con = connect(preservation_env["db"], read_only=True, load_vec=False)
    try:
        ok, _n, err = repo_audit.verify_chain(con)
    finally:
        con.close()
    assert ok, err


def test_destructive_apply_requires_explicit_flag(preservation_env: dict[str, Path]) -> None:
    """``apply`` MUST require either --dry-run or --execute. The library
    function reaches the same contract by accepting an explicit ``dry_run``
    bool — there's no defaultable middle ground. Both directions of the
    boolean are exercised by the suite; this test pins the API shape."""
    import inspect

    sig = inspect.signature(apply_manifest)
    assert "dry_run" in sig.parameters
    # No default — the caller MUST pass True or False.
    assert sig.parameters["dry_run"].default is inspect.Parameter.empty


def test_noise_paths_never_acted_on_via_stash(preservation_env: dict[str, Path]) -> None:
    """If a manifest somehow contained a noise path, the stash action
    refuses (the source-exists check fails for the synthetic case here;
    a real-FS test would also need the stash module to filter the path).

    The implementation lets the manifest reach apply — the policy
    reconciler is the prevention layer, not apply. This test asserts
    the safety net: noise paths produce a row-level error rather than a
    silent commit."""
    # Hand-edit the manifest to point at a noise path.
    text = preservation_env["manifest"].read_text()
    text = text.replace(str(preservation_env["source"]), "/Volumes/Backup/@eaDir/x")
    preservation_env["manifest"].write_text(text)
    result = apply_manifest(
        manifest_path=preservation_env["manifest"],
        machine_id="preservation",
        dry_run=False,
    )
    # The source doesn't exist → ManifestError → row errored, not applied.
    assert result.rows_applied == 0
    assert result.rows_errored == 1
