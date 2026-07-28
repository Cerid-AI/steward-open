# SPDX-License-Identifier: Apache-2.0

"""Preservation gate fixtures: a tmp inventory + a tmp tier root + a hand-built manifest."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from steward.core.manifest_io import write_manifest
from steward.core.model.manifest import Manifest, ManifestHeader, ManifestRow
from steward.infra.db.admin import migrate


@pytest.fixture
def preservation_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
    """A self-contained preservation test environment.

    Yields a dict with:
        ``db``       — migrated inventory.db
        ``tier``     — a tmp tier root (acts as a fake L2)
        ``source``   — a file under tier that the manifest will stash
        ``stash``    — the cooling-off destination
        ``manifest`` — a hand-built manifest TSV stashing source → stash
    """
    db = tmp_path / "inventory.db"
    tier = tmp_path / "tier"
    tier.mkdir()
    source = tier / "file.txt"
    source.write_bytes(b"keep me visible until cooling-off elapses")

    stash = tier / "_cooling-off-stash" / "test-run" / "file.txt"

    monkeypatch.setenv("STEWARD_DB_PATH", str(db))
    migrate(db)

    # Hand-build a manifest. The permanode_id is bogus for the apply test —
    # apply doesn't verify it against permanodes for the stash action (the
    # audit row carries it for traceability only).
    manifest_path = tmp_path / "plan.tsv"
    write_manifest(
        manifest_path,
        Manifest(
            header=ManifestHeader(
                produced_by_steward_version="test",
                produced_at=datetime.now(timezone.utc),
                policy_name="preservation-test",
                phase_name=None,
                manifest_run_id=f"preservation-{uuid4().hex[:8]}",
            ),
            rows=(
                ManifestRow(
                    action="stash",
                    permanode_id="0" * 32,
                    canonical_hash="0" * 64,
                    size_bytes=source.stat().st_size,
                    source_path=str(source),
                    source_tier="L2",
                    destination_path=str(stash),
                    destination_tier="L2",
                    rationale="preservation gate",
                ),
            ),
        ),
    )

    yield {
        "db": db,
        "tier": tier,
        "source": source,
        "stash": stash,
        "manifest": manifest_path,
    }
    # No teardown — tmp_path is auto-cleaned by pytest.
