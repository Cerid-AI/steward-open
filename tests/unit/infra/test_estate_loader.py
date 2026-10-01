# SPDX-License-Identifier: Apache-2.0

"""Estate file discovery, validation and caching."""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.core.errors import EstateError
from steward.infra.estate import loader

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "estate" / "two-host.yml"


@pytest.fixture
def default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home" / ".config" / "steward" / "estate.yml"
    monkeypatch.setattr(loader, "default_estate_path", lambda: path)
    return path


def test_no_file_is_legacy(default_path: Path) -> None:
    assert loader.estate_config_path() == default_path
    assert loader.find_estate() is None
    assert loader.current() is None


def test_default_path_is_read(default_path: Path) -> None:
    default_path.parent.mkdir(parents=True)
    default_path.write_text(FIXTURE.read_text())
    estate = loader.current()
    assert estate is not None
    assert set(estate.hosts) == {"studio", "mac-pro"}


def test_env_overrides_default(default_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    default_path.parent.mkdir(parents=True)
    default_path.write_text("not: [valid")
    monkeypatch.setenv(loader.ESTATE_CONFIG_ENV, str(FIXTURE))
    assert loader.estate_config_path() == FIXTURE
    estate = loader.current()
    assert estate is not None and estate.metadata == {"name": "two-host-lab"}


def test_env_naming_missing_file_is_an_error(
    default_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(loader.ESTATE_CONFIG_ENV, str(tmp_path / "typo.yml"))
    with pytest.raises(EstateError, match="not found"):
        loader.current()


def test_default_path_respects_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.undo()  # drop the suite-wide redirect
    monkeypatch.setenv("HOME", str(tmp_path))
    assert loader.default_estate_path() == tmp_path / ".config" / "steward" / "estate.yml"


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("version: [1", "YAML parse error"),
        ("- a\n- b\n", "must be a mapping"),
        ("version: 2\nhosts: {}\nvolumes: {}\n", "invalid estate"),
    ],
)
def test_invalid_files_raise(tmp_path: Path, text: str, match: str) -> None:
    path = tmp_path / "estate.yml"
    path.write_text(text)
    with pytest.raises(EstateError, match=match):
        loader.load_estate(path)


def test_cross_reference_errors_name_the_file(tmp_path: Path) -> None:
    path = tmp_path / "estate.yml"
    path.write_text(FIXTURE.read_text().replace("owner: studio", "owner: nobody", 1))
    with pytest.raises(EstateError, match=str(path)):
        loader.load_estate(path)


def test_current_is_cached_until_reset(default_path: Path) -> None:
    default_path.parent.mkdir(parents=True)
    default_path.write_text(FIXTURE.read_text())
    first = loader.current()
    default_path.unlink()
    assert loader.current() is first
    loader.reset_cache()
    assert loader.current() is None


def test_cache_follows_a_changed_config_path(default_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert loader.current() is None
    monkeypatch.setenv(loader.ESTATE_CONFIG_ENV, str(FIXTURE))
    assert loader.current() is not None
    monkeypatch.delenv(loader.ESTATE_CONFIG_ENV)
    assert loader.current() is None
