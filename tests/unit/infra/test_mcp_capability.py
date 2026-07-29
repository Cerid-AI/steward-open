# SPDX-License-Identifier: Apache-2.0
"""MCP capability mode + plan tokens (ADR-0016)."""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.infra.mcp.capability import (
    McpCapabilityError,
    McpMode,
    mcp_actor,
    mcp_max_files_cap,
    mcp_mode,
    require_mode,
)
from steward.infra.mcp.plan_tokens import (
    PlanTokenError,
    consume_plan_token,
    issue_plan_token,
    manifest_sha256,
    validate_plan_token,
)


def test_mcp_mode_default_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STEWARD_MCP_MODE", raising=False)
    assert mcp_mode() == McpMode.PLAN


def test_invalid_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEWARD_MCP_MODE", "writ")
    with pytest.raises(McpCapabilityError, match="invalid STEWARD_MCP_MODE"):
        mcp_mode()


def test_require_mode_blocks_write_when_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STEWARD_MCP_MODE", "plan")
    with pytest.raises(McpCapabilityError, match="apply_execute"):
        require_mode(McpMode.WRITE, tool="apply_execute")


def test_mcp_actor_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEWARD_MCP_ACTOR", "steward-mcp:boardroom")
    assert mcp_actor() == "steward-mcp:boardroom"


def test_max_files_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEWARD_MCP_MAX_FILES_CAP", "12")
    assert mcp_max_files_cap() == 12


def test_plan_token_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / "data"))
    manifest = tmp_path / "plan.tsv"
    manifest.write_text("# steward-manifest-v1\naction\tfoo\n", encoding="utf-8")
    rec = issue_plan_token(
        manifest_path=manifest,
        machine_id="m1",
        rows_total=1,
        rows_applied=1,
        max_files=1,
        dry_run_errors=0,
    )
    assert rec.manifest_sha256 == manifest_sha256(manifest.resolve())
    got = validate_plan_token(
        token=rec.token,
        manifest_path=manifest,
        machine_id="m1",
        max_files=1,
    )
    assert got.token == rec.token
    # Still valid before consume.
    validate_plan_token(
        token=rec.token,
        manifest_path=manifest,
        machine_id="m1",
        max_files=1,
    )
    consume_plan_token(token=rec.token)
    with pytest.raises(PlanTokenError, match="not found|consumed"):
        validate_plan_token(
            token=rec.token,
            manifest_path=manifest,
            machine_id="m1",
            max_files=1,
        )


def test_plan_token_rejects_content_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / "data"))
    manifest = tmp_path / "plan.tsv"
    manifest.write_text("v1\n", encoding="utf-8")
    rec = issue_plan_token(
        manifest_path=manifest,
        machine_id="m1",
        rows_total=0,
        rows_applied=0,
        max_files=None,
        dry_run_errors=0,
    )
    manifest.write_text("v2-changed\n", encoding="utf-8")
    with pytest.raises(PlanTokenError, match="content changed"):
        validate_plan_token(
            token=rec.token,
            manifest_path=manifest,
            machine_id="m1",
            max_files=1,
        )


def test_plan_token_enforces_dry_run_max_files_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / "data"))
    manifest = tmp_path / "plan.tsv"
    manifest.write_text("plan\n", encoding="utf-8")
    rec = issue_plan_token(
        manifest_path=manifest,
        machine_id="m1",
        rows_total=1,
        rows_applied=1,
        max_files=2,
        dry_run_errors=0,
    )
    with pytest.raises(PlanTokenError, match="exceeds dry-run bound"):
        validate_plan_token(
            token=rec.token,
            manifest_path=manifest,
            machine_id="m1",
            max_files=5,
        )
    # Equal or lower is OK.
    validate_plan_token(
        token=rec.token,
        manifest_path=manifest,
        machine_id="m1",
        max_files=2,
    )
