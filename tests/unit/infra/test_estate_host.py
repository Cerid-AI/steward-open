# SPDX-License-Identifier: Apache-2.0

"""Host identity resolution and the DB binding check."""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.core.errors import BindingMismatchError, EstateError, UnknownHostError
from steward.core.estate import Estate
from steward.infra.estate import host as host_mod
from steward.infra.estate import loader
from steward.infra.estate.host import (
    HOST_ENV,
    check_binding,
    current_identity,
    require_binding,
    require_known_host,
    resolve_host,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "estate" / "two-host.yml"
PINNED = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def estate() -> Estate:
    return loader.load_estate(FIXTURE)


def test_env_names_the_host(estate: Estate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HOST_ENV, "studio")
    ident = resolve_host(estate, hostname="MacPro-Host")
    assert (ident.host_id, ident.source, ident.known) == ("studio", "env", True)


def test_env_must_be_a_declared_host_id(estate: Estate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HOST_ENV, "Studio-Host")
    ident = resolve_host(estate, hostname="Studio-Host")
    assert ident.host_id is None
    assert "not a declared host" in ident.reason


@pytest.mark.parametrize("name", ["Studio-Host", "studio-host", "STUDIO-HOST.local", "Studio-Host.LOCAL"])
def test_hostname_matches_case_insensitively_without_dot_local(estate: Estate, name: str) -> None:
    ident = resolve_host(estate, hostname=name)
    assert (ident.host_id, ident.source) == ("studio", "hostname")
    assert ident.hostname == name.removesuffix(".local").removesuffix(".LOCAL")


def test_system_hostname_is_used_by_default(estate: Estate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_mod.socket, "gethostname", lambda: "MacPro-Host.local")
    assert host_mod.system_hostname() == "MacPro-Host"
    assert resolve_host(estate).host_id == "mac-pro"


def test_no_match_is_unknown(estate: Estate) -> None:
    ident = resolve_host(estate, hostname="Laptop")
    assert not ident.known
    assert "matches no host" in ident.reason and HOST_ENV in ident.reason


def test_hostname_claimed_by_two_hosts_is_unknown(estate: Estate) -> None:
    hosts = dict(estate.hosts)
    hosts["mac-pro"] = hosts["mac-pro"].model_copy(update={"hostnames": ("MacPro-Host", "studio-host")})
    twin = estate.model_copy(update={"hosts": hosts})
    ident = resolve_host(twin, hostname="Studio-Host")
    assert not ident.known
    assert "several hosts" in ident.reason


def test_require_known_host(estate: Estate) -> None:
    assert require_known_host(resolve_host(estate, hostname="MacPro-Host")) == "mac-pro"
    with pytest.raises(UnknownHostError, match="refusing to mutate") as exc:
        require_known_host(resolve_host(estate, hostname="Laptop"))
    assert isinstance(exc.value, EstateError)


def test_current_identity_is_none_without_an_estate() -> None:
    assert current_identity() is None


def test_current_identity_with_an_estate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(loader.ESTATE_CONFIG_ENV, str(FIXTURE))
    monkeypatch.setenv(HOST_ENV, "mac-pro")
    ident = current_identity()
    assert ident is not None and ident.host_id == "mac-pro"


def test_binding_statuses(estate: Estate) -> None:
    mac_pro, studio = estate.hosts["mac-pro"], estate.hosts["studio"]
    assert check_binding(mac_pro, PINNED).status == "bound"
    assert check_binding(mac_pro, None).status == "missing"
    assert check_binding(studio, "anything").status == "unpinned"
    assert check_binding(studio, None).status == "unpinned"
    mismatch = check_binding(mac_pro, "11111111-1111-4111-8111-111111111111")
    assert mismatch.status == "mismatch" and not mismatch.ok
    assert PINNED in mismatch.describe()


def test_require_binding_refuses_only_a_mismatch(estate: Estate) -> None:
    mac_pro = estate.hosts["mac-pro"]
    require_binding(check_binding(mac_pro, PINNED))
    require_binding(check_binding(mac_pro, None))
    with pytest.raises(BindingMismatchError, match="refusing to mutate"):
        require_binding(check_binding(mac_pro, "11111111-1111-4111-8111-111111111111"))
