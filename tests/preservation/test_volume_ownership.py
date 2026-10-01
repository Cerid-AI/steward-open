# SPDX-License-Identifier: Apache-2.0

"""Preservation invariant — under ``enforce``, apply never mutates a volume this host does not own.

Two hosts mount the same NAS at the same path; only the owner may change it.
The anonymised two-host estate is relocated under ``tmp_path``. Don't relax
this without an ADR.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest

from steward.core.manifest_io import write_manifest
from steward.core.model.manifest import Manifest, ManifestHeader, ManifestRow
from steward.infra.db.apply import ApplyRefused, apply_manifest
from steward.infra.db.connect import connect

pytestmark = pytest.mark.preservation

PINNED = "00000000-0000-4000-8000-000000000002"


def _tree(root: Path) -> dict[str, tuple[int, int, bytes]]:
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes())
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _manifest(path: Path, rows: tuple[ManifestRow, ...]) -> Path:
    write_manifest(
        path,
        Manifest(
            header=ManifestHeader(
                produced_by_steward_version="test",
                produced_at=datetime.now(timezone.utc),
                policy_name="preservation-ownership",
                manifest_run_id="preservation-ownership",
            ),
            rows=rows,
        ),
    )
    return path


def _row(action: str, src: Path, dst: Path | None = None) -> ManifestRow:
    return ManifestRow(
        action=action,  # type: ignore[arg-type]
        permanode_id="0" * 32,
        canonical_hash="0" * 64,
        size_bytes=src.stat().st_size if src.exists() else 0,
        source_path=str(src),
        source_tier="x",
        destination_path=str(dst) if dst is not None else None,
        destination_tier="x" if dst is not None else None,
        rationale="preservation gate",
    )


@pytest.fixture
def client(tmp_path: Path, tmp_estate: Callable[..., Path], monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """``mac-pro`` under ``enforce``, with files on the NAS share it only reads and on its own Level 2."""
    from steward.infra.db.admin import migrate

    root = tmp_estate("mac-pro", "enforce")
    db = tmp_path / "inventory.db"
    monkeypatch.setenv("STEWARD_DB_PATH", str(db))
    migrate(db)
    con = connect(db, load_vec=False)
    try:
        con.execute("UPDATE meta SET value = ? WHERE key = 'machine_id'", (PINNED,))
        con.commit()
    finally:
        con.close()
    nas = root / "Level_3a"
    (nas / "photos").mkdir(parents=True)
    (nas / "photos" / "a.jpg").write_bytes(b"nas copy a")
    (nas / "photos" / "b.jpg").write_bytes(b"nas copy b")
    own = root / "Level 2" / "docs"
    own.mkdir(parents=True)
    (own / "c.txt").write_bytes(b"client copy c")
    return {"db": db, "root": root, "nas": nas, "own": own}


def _cases(env: dict[str, Path]) -> dict[str, tuple[ManifestRow, ...]]:
    nas, own = env["nas"], env["own"]
    a, b, c = nas / "photos" / "a.jpg", nas / "photos" / "b.jpg", own / "c.txt"
    return {
        "stash-on-nas": (_row("stash", a, nas / "_cooling-off-stash" / "r" / "a.jpg"),),
        "retire-on-nas": (_row("retire_direct", b),),
        "promote-onto-nas": (_row("promote", c, nas / "photos" / "c.txt"),),
        "stash-off-nas": (_row("stash", a, env["root"] / "Level 2" / "_cooling-off-stash" / "a.jpg"),),
        "nas-manifest": (_row("nas_manifest", a),),
        "mixed-with-owned-row": (
            _row("stash", c, own / "_cooling-off-stash" / "c.txt"),
            _row("retire_direct", a),
        ),
    }


@pytest.mark.parametrize(
    "case",
    ["stash-on-nas", "retire-on-nas", "promote-onto-nas", "stash-off-nas", "nas-manifest", "mixed-with-owned-row"],
)
def test_enforce_never_mutates_a_foreign_volume(client: dict[str, Path], tmp_path: Path, case: str) -> None:
    manifest = _manifest(tmp_path / f"{case}.tsv", _cases(client)[case])
    before = _tree(client["root"])

    with pytest.raises(ApplyRefused) as exc_info:
        apply_manifest(manifest_path=manifest, machine_id=PINNED, dry_run=False)

    assert exc_info.value.result.rows_applied == 0
    assert _tree(client["root"]) == before
    con = connect(client["db"], read_only=True, load_vec=False)
    try:
        actions = {row[0] for row in con.execute("SELECT action FROM audit_log")}
    finally:
        con.close()
    assert actions == {"apply_rejected_foreign_volume"}  # refusals only: no row ran, not even apply_start
