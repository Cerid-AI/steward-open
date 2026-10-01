# SPDX-License-Identifier: Apache-2.0

"""``docs/estate.example.yml`` validates and behaves as its comments say."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from steward.cli.main import app
from steward.core.estate import Estate, decide
from steward.infra.estate.check import check_mounts
from steward.infra.estate.loader import load_estate

EXAMPLE = Path(__file__).resolve().parents[3] / "docs" / "estate.example.yml"


@pytest.fixture(scope="module")
def estate() -> Estate:
    return load_estate(EXAMPLE)


def test_estate_validate_cli_accepts_the_example() -> None:
    result = CliRunner().invoke(app, ["estate", "validate", str(EXAMPLE)])
    assert result.exit_code == 0, result.output
    assert "2 hosts" in " ".join(result.output.split())


def test_the_shared_nas_path_is_writable_only_by_its_owner(estate: Estate) -> None:
    path = "/Volumes/nas-share/projects/a.bin"
    assert decide("stash", path, "primary", estate).allowed
    refused = decide("stash", path, "client", estate)
    assert not refused.allowed and refused.access == "ro"
    assert not decide("scan", path, "client", estate).allowed


def test_the_time_machine_share_takes_replicas_only_in_each_hosts_folder(estate: Estate) -> None:
    for host in ("primary", "client"):
        assert decide("replicate-destination", f"/Volumes/nas-backup/_steward-mirror/{host}/db", host, estate).allowed
        assert decide("promote-source", "/Volumes/nas-backup/x", host, estate).allowed
        assert not decide("stash", "/Volumes/nas-backup/x", host, estate).allowed
    assert not decide(
        "replicate-destination", "/Volumes/nas-backup/_steward-mirror/primary/db", "client", estate
    ).allowed


def test_every_declared_mount_present_checks_clean(estate: Estate) -> None:
    for host in estate.hosts:
        live = {
            m.path
            for v in estate.volumes.values()
            if (m := v.mounts.get(host)) is not None and m.access != "forbid" and m.path != "/"
        }
        assert check_mounts(estate, host, live=live) == [], host
