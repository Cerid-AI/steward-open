# SPDX-License-Identifier: Apache-2.0

"""Unit/integration-light tests for collect_estate_health composition."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from steward.core.health import DEFAULT_CHECK_FAIL_ON, evaluate_fail_on
from steward.infra.db.admin import migrate, resolve_machine_id
from steward.infra.db.connect import connect
from steward.infra.health import collect_estate_health, estate_health_to_dict
from steward.infra.scanner.incremental import scan_paths


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "inventory.db"
    migrate(p)
    return p


def test_collect_empty_db_quick(db_path: Path) -> None:
    report = collect_estate_health(
        db_path=db_path,
        quick=True,
        probes=False,
        include_fp=False,
        include_schedule=False,
        include_imports=False,
    )
    assert report.machine_id
    assert report.quick is True
    assert report.inventory.audit_skipped is True
    assert report.stash.source == "skipped"
    assert report.fp.present is False
    # no finished scans → stale_scan fail
    names = {c.name: c.level for c in report.checks}
    assert names.get("stale_scan") == "fail"
    assert names.get("broken_audit") == "skipped"
    assert names.get("stash_overdue") == "skipped"
    d = estate_health_to_dict(report)
    assert d["overall"] == report.overall
    assert "checks" in d


def test_collect_with_finished_scan_passes_stale_scan(
    db_path: Path, tmp_path: Path
) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    f = root / "a.txt"
    f.write_bytes(b"estate health scan fixture")
    machine_id = resolve_machine_id(db_path)
    con = connect(db_path)
    try:
        scan_paths(con=con, paths=[f], machine_id=machine_id)
        con.commit()
    finally:
        con.close()

    now = datetime.now(timezone.utc)
    report = collect_estate_health(
        db_path=db_path,
        quick=True,
        probes=False,
        include_fp=False,
        include_schedule=False,
        now=now,
    )
    assert report.scan_freshness
    assert all(r.level == "ok" for r in report.scan_freshness)
    stale = next(c for c in report.checks if c.name == "stale_scan")
    assert stale.level == "ok"
    failed = evaluate_fail_on(report, DEFAULT_CHECK_FAIL_ON)
    # broken_audit/stash skipped cannot fail; rollup unknown/live ok
    assert all(c.name != "stale_scan" for c in failed)


def test_collect_marks_old_scan_stale(db_path: Path) -> None:
    mid = resolve_machine_id(db_path)
    old = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat(timespec="seconds")
    con = connect(db_path)
    try:
        con.execute(
            """
            INSERT INTO scan_runs (
                root_path, machine_id, started_at, finished_at,
                workers, include_containers,
                files_walked, files_hashed, files_skipped, bytes_hashed, errors
            ) VALUES (?, ?, ?, ?, 1, 0, 1, 1, 0, 10, 0)
            """,
            ("/Volumes/Level 2/old", mid, old, old),
        )
        con.commit()
    finally:
        con.close()

    report = collect_estate_health(
        db_path=db_path,
        quick=True,
        probes=False,
        include_fp=False,
        include_schedule=False,
    )
    assert any(r.level == "fail" for r in report.scan_freshness)
    failed = evaluate_fail_on(report, frozenset({"stale_scan"}))
    assert len(failed) == 1
