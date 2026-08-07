# SPDX-License-Identifier: Apache-2.0

"""Unit tests for pure fleet SLA evaluation (ADR-0021)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from steward.core.fleet import (
    DEFAULT_FLEET_CHECK_FAIL_ON,
    DEFAULT_FLEET_THRESHOLDS,
    FAIL_ON_ATTACHED_MISSING,
    FAIL_ON_ENVELOPE_SLA,
    FAIL_ON_FLEET_CHAIN_STALE,
    FAIL_ON_FLEET_STALE_SCAN,
    KNOWN_FLEET_FAIL_ON_TOKENS,
    FleetHealthMatrix,
    FleetThresholds,
    MachineHealthRow,
    age_hours_from_iso,
    build_envelope_sla,
    build_fleet_checks,
    chain_level_for_attached,
    chain_level_for_local,
    envelope_level_for_attached,
    envelope_level_for_local,
    evaluate_fleet_fail_on,
    row_rollup_level,
    scan_level_for_row,
    validate_fleet_fail_on_tokens,
)
from steward.core.health.evaluate import level_for_age

NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def _row(**kwargs: object) -> MachineHealthRow:
    base: dict[str, object] = {
        "machine_id": "m1",
        "source": "local",
        "is_current": True,
        "claim_count": 1,
        "current_claim_count": 1,
        "scan_level": "ok",
        "chain_level": "unknown",
        "envelope_level": "ok",
        "level": "ok",
    }
    base.update(kwargs)
    return MachineHealthRow(**base)  # type: ignore[arg-type]


# ─────────────────────── age / level boundaries ──────────────────────────


def test_level_for_age_boundaries() -> None:
    thr = DEFAULT_FLEET_THRESHOLDS
    assert level_for_age(None, thr.scan_max_age_hours, missing_level="fail") == "fail"
    assert level_for_age(0.0, thr.scan_max_age_hours) == "ok"
    assert level_for_age(thr.scan_max_age_hours, thr.scan_max_age_hours) == "ok"
    assert level_for_age(thr.scan_max_age_hours + 0.01, thr.scan_max_age_hours) == "fail"


def test_scan_level_missing_finished() -> None:
    assert scan_level_for_row(None, has_finished=False) == "fail"
    assert scan_level_for_row(10.0, has_finished=True) == "ok"
    assert scan_level_for_row(200.0, has_finished=True) == "fail"


def test_envelope_local_missing_is_warn() -> None:
    assert envelope_level_for_local(None) == "warn"
    assert envelope_level_for_local(10.0) == "ok"
    assert envelope_level_for_local(200.0) == "fail"


def test_envelope_attached_missing_payload_fail() -> None:
    assert (
        envelope_level_for_attached(10.0, payload_exists=False) == "fail"
    )
    assert envelope_level_for_attached(10.0, payload_exists=True) == "ok"
    assert (
        envelope_level_for_attached(30 * 24 + 1, payload_exists=True) == "fail"
    )


def test_chain_local_quick_unknown() -> None:
    assert chain_level_for_local(quick=True) == "unknown"
    assert chain_level_for_local(quick=False, audit_ok=True) == "ok"
    assert chain_level_for_local(quick=False, audit_ok=False) == "fail"


def test_chain_attached_matrix() -> None:
    thr = DEFAULT_FLEET_THRESHOLDS
    assert (
        chain_level_for_attached(
            payload_exists=False,
            chain_verified_at=None,
            chain_age_hours=None,
            thresholds=thr,
        )
        == "fail"
    )
    assert (
        chain_level_for_attached(
            payload_exists=True,
            chain_verified_at=None,
            chain_age_hours=None,
            thresholds=thr,
        )
        == "warn"
    )
    assert (
        chain_level_for_attached(
            payload_exists=True,
            chain_verified_at=_iso(1),
            chain_age_hours=1.0,
            thresholds=thr,
        )
        == "ok"
    )
    assert (
        chain_level_for_attached(
            payload_exists=True,
            chain_verified_at=_iso(40 * 24),
            chain_age_hours=40 * 24,
            thresholds=thr,
        )
        == "warn"
    )


def test_age_hours_from_iso() -> None:
    assert age_hours_from_iso(None, now=NOW) is None
    age = age_hours_from_iso(_iso(5), now=NOW)
    assert age is not None
    assert 4.9 < age < 5.1


# ─────────────────────── rollup + envelope SLA ──────────────────────────


def test_row_rollup_worst() -> None:
    r = _row(scan_level="ok", chain_level="warn", envelope_level="fail")
    assert row_rollup_level(r) == "fail"


def test_build_envelope_sla_attached_stale() -> None:
    rows = (
        _row(
            machine_id="local",
            source="local",
            is_current=True,
            envelope_at=_iso(10),
            envelope_age_hours=10.0,
            envelope_level="ok",
        ),
        _row(
            machine_id="peer",
            source="attached",
            is_current=False,
            envelope_level="fail",
            payload_exists=False,
        ),
    )
    sla = build_envelope_sla(rows)
    assert sla.attached_count == 1
    assert sla.attached_missing_payload == 1
    assert sla.level == "fail"


# ─────────────────────── fail-on evaluation ──────────────────────────


def _matrix(rows: tuple[MachineHealthRow, ...]) -> FleetHealthMatrix:
    from dataclasses import asdict

    thr = DEFAULT_FLEET_THRESHOLDS
    # recompute levels
    fixed = []
    for r in rows:
        fixed.append(
            MachineHealthRow(
                **{
                    **asdict(r),
                    "level": row_rollup_level(r),
                }
            )
        )
    rows_t = tuple(fixed)
    sla = build_envelope_sla(rows_t)
    checks = build_fleet_checks(rows_t, sla, thresholds=thr)
    from steward.core.fleet.evaluate import compute_fleet_overall

    return FleetHealthMatrix(
        generated_at=NOW.isoformat(),
        local_machine_id="local",
        overall=compute_fleet_overall(rows_t, checks),
        thresholds=thr,
        rows=rows_t,
        envelope_sla=sla,
        checks=tuple(checks),
    )


def test_evaluate_fleet_stale_scan_fail() -> None:
    m = _matrix(
        (
            _row(
                machine_id="local",
                scan_level="fail",
                last_scan_finished_at=None,
                envelope_level="ok",
            ),
        )
    )
    failed = evaluate_fleet_fail_on(m, {FAIL_ON_FLEET_STALE_SCAN})
    assert len(failed) == 1
    assert failed[0].name == FAIL_ON_FLEET_STALE_SCAN


def test_evaluate_envelope_sla_and_attached_missing() -> None:
    m = _matrix(
        (
            _row(
                machine_id="local",
                source="local",
                is_current=True,
                scan_level="ok",
                envelope_level="ok",
            ),
            _row(
                machine_id="peer",
                source="attached",
                is_current=False,
                scan_level="ok",
                chain_level="fail",
                envelope_level="fail",
                payload_exists=False,
            ),
        )
    )
    failed = evaluate_fleet_fail_on(
        m,
        {FAIL_ON_ENVELOPE_SLA, FAIL_ON_ATTACHED_MISSING, FAIL_ON_FLEET_CHAIN_STALE},
    )
    names = {c.name for c in failed}
    assert FAIL_ON_ENVELOPE_SLA in names
    assert FAIL_ON_ATTACHED_MISSING in names
    assert FAIL_ON_FLEET_CHAIN_STALE in names


def test_evaluate_ok_when_fresh() -> None:
    m = _matrix(
        (
            _row(
                machine_id="local",
                scan_level="ok",
                chain_level="unknown",
                envelope_level="ok",
                envelope_at=_iso(1),
                envelope_age_hours=1.0,
            ),
        )
    )
    failed = evaluate_fleet_fail_on(m, DEFAULT_FLEET_CHECK_FAIL_ON)
    assert failed == []


def test_validate_unknown_tokens() -> None:
    assert validate_fleet_fail_on_tokens(["not_a_token"]) == ["not_a_token"]
    assert validate_fleet_fail_on_tokens(KNOWN_FLEET_FAIL_ON_TOKENS) == []


def test_default_thresholds_match_adr() -> None:
    thr = DEFAULT_FLEET_THRESHOLDS
    assert thr.scan_max_age_hours == 168.0
    assert thr.envelope_max_age_hours == 192.0
    assert thr.attached_max_age_days == 30.0
    assert thr.chain_verify_max_age_days == 30.0


def test_custom_thresholds() -> None:
    thr = FleetThresholds(scan_max_age_hours=1.0)
    assert scan_level_for_row(2.0, thresholds=thr, has_finished=True) == "fail"
