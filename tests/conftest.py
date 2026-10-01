# SPDX-License-Identifier: Apache-2.0

"""Suite-wide fixtures."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from steward.infra.estate import loader

TWO_HOST_ESTATE = Path(__file__).resolve().parent / "fixtures" / "estate" / "two-host.yml"


@pytest.fixture(autouse=True)
def _no_operator_estate(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the operator's ~/.config/steward/estate.yml (and STEWARD_HOST) out of every test."""
    absent = tmp_path_factory.getbasetemp() / "no-estate" / "estate.yml"
    monkeypatch.setattr(loader, "default_estate_path", lambda: Path(absent))
    monkeypatch.delenv(loader.ESTATE_CONFIG_ENV, raising=False)
    monkeypatch.delenv("STEWARD_HOST", raising=False)
    loader.reset_cache()
    yield
    loader.reset_cache()


@pytest.fixture
def tmp_estate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    """Install the two-host fixture estate with ``/Volumes`` relocated under ``tmp_path``.

    ``tmp_estate(host, enforcement)`` writes the rewritten estate, points
    ``STEWARD_ESTATE_CONFIG`` / ``STEWARD_HOST`` at it and returns the
    relocated ``Volumes`` root, so ``root / "Level_3a"`` is the NAS share as
    that host sees it. Both hosts still see the NAS at the same path.
    """

    def install(host: str, enforcement: str = "enforce") -> Path:
        root = tmp_path / "Volumes"
        root.mkdir(exist_ok=True)
        text = TWO_HOST_ESTATE.read_text(encoding="utf-8").replace("/Volumes/", f"{root}/")
        text = text.replace("enforcement: report", f"enforcement: {enforcement}")
        config = tmp_path / "estate.yml"
        config.write_text(text, encoding="utf-8")
        monkeypatch.setenv(loader.ESTATE_CONFIG_ENV, str(config))
        monkeypatch.setenv("STEWARD_HOST", host)
        loader.reset_cache()
        return root

    return install
