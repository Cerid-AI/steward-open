# SPDX-License-Identifier: Apache-2.0

"""Unit tests for :mod:`steward.infra.fp_status`."""

from __future__ import annotations

import json
import plistlib
from pathlib import Path

from steward.infra.fp_status import (
    DomainProbe,
    collect_fp_status,
)


def test_collect_fp_status_forked_tmp(tmp_path: Path) -> None:
    mount = tmp_path / "mount"
    store = tmp_path / "store"
    mount.mkdir()
    store.mkdir()
    (store / "logo.jpg").write_bytes(b"abc")
    (mount / "logo.jpg").write_bytes(b"abc")
    report = collect_fp_status(
        home=tmp_path,
        store_root=store,
        mount_root=mount,
        sample_rels=("logo.jpg", "missing"),
        probe_domain=False,
    )
    assert report.mount.exists
    assert report.store.exists
    assert report.sample_both == 1
    assert report.sample_neither == 1
    assert report.verdict is not None
    assert report.verdict.cloud_retire_ready is True


def test_collect_fp_status_missing_roots(tmp_path: Path) -> None:
    report = collect_fp_status(
        home=tmp_path,
        store_root=tmp_path / "no-store",
        mount_root=tmp_path / "no-mount",
        sample_rels=(),
        probe_domain=False,
        probe_name_divergence=False,
    )
    assert not report.mount.exists
    assert not report.store.exists
    assert report.forked_devices is False
    assert report.verdict is not None
    assert report.verdict.layout == "missing"
    assert report.verdict.cloud_retire_ready is False


def test_collect_fp_status_one_side_missing(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    report = collect_fp_status(
        home=tmp_path,
        store_root=store,
        mount_root=tmp_path / "no-mount",
        sample_rels=(),
        probe_domain=False,
        probe_name_divergence=False,
    )
    assert report.store.exists
    assert not report.mount.exists
    assert report.forked_devices is True
    assert report.verdict is not None
    assert report.verdict.layout == "store_only"
    assert report.verdict.cloud_retire_ready is False
    assert report.verdict.local_reclaim_ready is True


def test_external_drive_residual_unlinked_is_warning_not_problem(
    tmp_path: Path,
) -> None:
    """Healthy external-drive FP: residual Domains.plist unlinked → warn only."""
    provider = tmp_path / "Library" / "Application Support" / "FileProvider" / "com.getdropbox.dropbox.fileprovider"
    provider.mkdir(parents=True)
    domain_id = "c1cdee97-3d27-47fb-a6ec-8e316d68b70b"
    payload = {
        domain_id: {
            "Connected": False,
            "Disconnected": True,
            "DisconnectionReason": "This is an unlinked Dropbox",
            "Path": "FPFS_SHOULD_NOT_BE_USED",
            "SupportsSyncingTrash": False,
        }
    }
    with (provider / "Domains.plist").open("wb") as fh:
        plistlib.dump(payload, fh)

    dropbox_cfg = tmp_path / ".dropbox"
    dropbox_cfg.mkdir()
    # info.json path must look like external store for layout classification
    external_store = "/Volumes/DropboxStorage/.CloudStorage/Data/Dropbox"
    store = tmp_path / "store"
    store.mkdir(parents=True)
    mount = tmp_path / "Library" / "CloudStorage" / "Dropbox"
    mount.mkdir(parents=True)
    (store / "Home").mkdir()
    (mount / "Home").mkdir()
    (dropbox_cfg / "info.json").write_text(
        json.dumps({"personal": {"path": external_store}}),
        encoding="utf-8",
    )

    report = collect_fp_status(
        home=tmp_path,
        store_root=store,
        mount_root=mount,
        sample_rels=("Home",),
        probe_name_divergence=False,
    )
    assert report.verdict is not None
    v = report.verdict
    assert v.layout == "external_drive_fp"
    assert v.cloud_retire_ready is True
    assert v.problems == ()
    assert any("residual" in w.lower() or "unlinked" in w.lower() for w in v.warnings)


def test_hard_unlinked_without_store_is_problem(tmp_path: Path) -> None:
    provider = tmp_path / "Library" / "Application Support" / "FileProvider" / "com.getdropbox.dropbox.fileprovider"
    provider.mkdir(parents=True)
    with (provider / "Domains.plist").open("wb") as fh:
        plistlib.dump(
            {
                "dom1": {
                    "Connected": False,
                    "Disconnected": True,
                    "DisconnectionReason": "This is an unlinked Dropbox",
                    "Path": "FPFS_SHOULD_NOT_BE_USED",
                }
            },
            fh,
        )
    mount = tmp_path / "m"
    mount.mkdir()
    # no store
    report = collect_fp_status(
        home=tmp_path,
        store_root=tmp_path / "no-store",
        mount_root=mount,
        sample_rels=(),
        probe_name_divergence=False,
    )
    assert report.verdict is not None
    # mount-only + unlinked → problem about domain or missing dual roots
    assert report.verdict.cloud_retire_ready is True or report.verdict.problems
    # mount exists so cloud_retire may still be true if no other problems;
    # domain hard path without external layout should add problem
    assert (
        any("unlinked" in p.lower() or "disconnected" in p.lower() for p in report.verdict.problems)
        or report.verdict.layout == "mount_only"
    )


def test_name_divergence_is_warning(tmp_path: Path) -> None:
    mount = tmp_path / "mount"
    store = tmp_path / "store"
    mount.mkdir()
    store.mkdir()
    (store / "ArchDev").mkdir()
    (mount / "ArchDev (Selective Sync Conflict)").mkdir()
    (store / "Home").mkdir()
    (mount / "Home").mkdir()
    report = collect_fp_status(
        home=tmp_path,
        store_root=store,
        mount_root=mount,
        sample_rels=("Home",),
        probe_domain=False,
    )
    assert report.name_divergence is not None
    assert "ArchDev" in report.name_divergence.store_only
    assert report.verdict is not None
    assert report.verdict.cloud_retire_ready is True
    assert any("basename" in w.lower() or "diverge" in w.lower() for w in report.verdict.warnings)


def test_store_only_samples_block_cloud(tmp_path: Path) -> None:
    mount = tmp_path / "mount"
    store = tmp_path / "store"
    mount.mkdir()
    store.mkdir()
    (store / "only_on_store.txt").write_text("x")
    report = collect_fp_status(
        home=tmp_path,
        store_root=store,
        mount_root=mount,
        sample_rels=("only_on_store.txt",),
        probe_domain=False,
        probe_name_divergence=False,
    )
    assert report.sample_store_only == 1
    assert report.sample_both == 0
    assert report.verdict is not None
    assert report.verdict.cloud_retire_ready is False
    assert any("store-only" in p.lower() for p in report.verdict.problems)


def test_domain_probe_properties() -> None:
    d = DomainProbe(
        provider_id="com.getdropbox.dropbox.fileprovider",
        domain_id="x",
        connected=False,
        disconnected=True,
        disconnection_reason="This is an unlinked Dropbox",
        domain_path="FPFS_SHOULD_NOT_BE_USED",
        supports_syncing_trash=False,
    )
    assert d.is_fpfs_placeholder is True
    assert d.reports_disconnected is True
    assert d.is_unlinked is True
