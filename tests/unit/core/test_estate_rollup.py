# SPDX-License-Identifier: Apache-2.0

"""Which tiers a host's claims may contribute to an estate-wide rollup."""

from __future__ import annotations

from steward.core.estate import Estate, default_estate
from steward.core.estate.rollup import foreign_tiers, host_for_machine


def test_client_does_not_speak_for_the_primarys_volumes(two_host_estate: Estate) -> None:
    # boot is owned on both hosts, so it stays the client's own.
    assert foreign_tiers(two_host_estate, "mac-pro") == {"Work", "Cache", "StudioPhotos", "L3a", "Backup"}


def test_primary_does_not_speak_for_the_clients_volumes(two_host_estate: Estate) -> None:
    assert foreign_tiers(two_host_estate, "studio") == {"L1", "L1w", "L2", "DropboxStorage", "BOOTCAMP"}


def test_legacy_estate_has_no_foreign_tiers() -> None:
    estate = default_estate()
    assert foreign_tiers(estate, next(iter(estate.hosts))) == frozenset()


def test_host_for_machine_uses_pinned_ids(two_host_estate: Estate) -> None:
    assert host_for_machine(two_host_estate, "00000000-0000-4000-8000-000000000002") == "mac-pro"
    assert host_for_machine(two_host_estate, "11111111-0000-4000-8000-000000000000") is None
