# SPDX-License-Identifier: Apache-2.0

"""Data-dir precedence and the unmounted-volume guard."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from steward.core.errors import DataDirUnavailableError
from steward.infra.db import admin, settings
from steward.infra.db.connect import connect
from steward.infra.estate import loader

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "estate" / "two-host.yml"
# A name no CI runner or operator host has mounted.
GHOST = "/Volumes/steward-test-unmounted"


@pytest.fixture
def no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STEWARD_DB_PATH", raising=False)
    monkeypatch.delenv("STEWARD_DATA_DIR", raising=False)


@pytest.fixture
def with_estate(monkeypatch: pytest.MonkeyPatch, no_env: None) -> None:
    monkeypatch.setenv(loader.ESTATE_CONFIG_ENV, str(FIXTURE))


def _platform_default() -> Path:
    return settings.user_data_path("steward", appauthor=False)


def test_default_without_estate(no_env: None) -> None:
    assert settings.resolve_data_dir() == (_platform_default(), "default")
    assert settings.inventory_db_path() == _platform_default() / "inventory.db"


def test_estate_data_dir_for_this_host(with_estate: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEWARD_HOST", "mac-pro")
    assert settings.resolve_data_dir() == (Path("/Volumes/Level 2/steward-data"), "estate")
    assert settings.inventory_db_path() == Path("/Volumes/Level 2/steward-data/inventory.db")


def test_estate_hostname_resolution_feeds_data_dir(with_estate: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("steward.infra.estate.host.socket.gethostname", lambda: "studio-host.local")
    assert settings.data_dir() == Path("/Volumes/Work/steward-data")


def test_data_dir_env_beats_estate(with_estate: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STEWARD_HOST", "mac-pro")
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path))
    assert settings.resolve_data_dir() == (tmp_path, "STEWARD_DATA_DIR")
    assert settings.inventory_db_path() == tmp_path / "inventory.db"


def test_db_path_env_beats_everything(with_estate: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STEWARD_HOST", "mac-pro")
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STEWARD_DB_PATH", str(tmp_path / "x.db"))
    assert settings.inventory_db_path() == tmp_path / "x.db"


def test_unknown_host_falls_back_to_default(with_estate: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("steward.infra.estate.host.socket.gethostname", lambda: "Laptop")
    assert settings.resolve_data_dir() == (_platform_default(), "default")


def test_host_without_data_dir_falls_back_to_default(
    no_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "estate.yml"
    path.write_text(FIXTURE.read_text().replace("data_dir: /Volumes/Work/steward-data", ""))
    monkeypatch.setenv(loader.ESTATE_CONFIG_ENV, str(path))
    monkeypatch.setenv("STEWARD_HOST", "studio")
    assert settings.resolve_data_dir() == (_platform_default(), "default")


# ─────────────────────── mount guard ──────────────────────────


@pytest.fixture
def unmounted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []

    def fake_ismount(path: object) -> bool:
        asked.append(os.fspath(path))  # type: ignore[arg-type]
        return False

    monkeypatch.setattr(settings.os.path, "ismount", fake_ismount)
    return asked


@pytest.mark.parametrize("path", [GHOST, f"{GHOST}/steward-data", f"{GHOST}/a/b/steward-data"])
def test_guard_refuses_under_an_unmounted_volume(unmounted: list[str], path: str) -> None:
    with pytest.raises(DataDirUnavailableError, match=f"{GHOST} is not mounted"):
        settings.assert_data_dir_mounted(path)
    assert unmounted == [GHOST]


def test_guard_passes_a_mounted_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.os.path, "ismount", lambda p: os.fspath(p) == GHOST)
    settings.assert_data_dir_mounted(f"{GHOST}/steward-data")


@pytest.mark.parametrize("path", ["/", "/Volumes", "/Users/operator/steward-data", "relative/dir"])
def test_guard_ignores_paths_outside_volumes(unmounted: list[str], path: str) -> None:
    settings.assert_data_dir_mounted(path)
    assert unmounted == []


def test_guard_follows_symlinks_out_of_volumes(
    unmounted: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "boot"
    real.mkdir()
    original = os.path.realpath

    def fake_realpath(p: object) -> str:
        if os.fspath(p).startswith("/Volumes/Boot Alias"):  # type: ignore[call-overload]
            return str(real / "steward-data")
        return original(p)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr(settings.os.path, "realpath", fake_realpath)
    settings.assert_data_dir_mounted("/Volumes/Boot Alias/steward-data")
    assert unmounted == []


def test_connect_refuses_before_creating_anything(unmounted: list[str]) -> None:
    target = Path(GHOST) / "steward-data" / "inventory.db"
    with pytest.raises(DataDirUnavailableError):
        connect(target)
    assert not Path(GHOST).exists()


def test_connect_outside_volumes_is_unaffected(unmounted: list[str], tmp_path: Path) -> None:
    db = tmp_path / "inventory.db"
    connect(db, load_vec=False).close()
    connect(db, read_only=True, load_vec=False).close()


def test_migrate_refuses_before_creating_anything(unmounted: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEWARD_DB_PATH", f"{GHOST}/steward-data/inventory.db")
    with pytest.raises(DataDirUnavailableError):
        admin.migrate()
    assert not Path(GHOST).exists()


def test_read_machine_id_never_creates(tmp_path: Path) -> None:
    target = tmp_path / "inventory.db"
    assert admin.read_machine_id(target) is None
    assert not target.exists()
    migrated = admin.migrate(target)
    assert admin.read_machine_id(target) == migrated.machine_id
