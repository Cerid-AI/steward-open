# SPDX-License-Identifier: Apache-2.0

"""Unit tests for File Provider domain sync-health parsing + grading.

The fixture text is a trimmed REAL capture from 2026-08-16 — the day the
iCloud Drive domain was found at error generation 628 after a 46-day retry
loop that silently filled the boot volume through nsurlsessiond's DataVault.
Every grading test plants that incident's shape and asserts the check goes
red on it; the converse tests pin the healthy shapes so the check cannot nag.
"""

from __future__ import annotations

from steward.core.health import (
    DEFAULT_THRESHOLDS,
    FPDomainHealth,
    check_fp_sync_stuck,
    fp_domain_level,
)
from steward.infra.health.fp_domains import parse_fp_dump

# Trimmed from the live `fileproviderctl dump -l` of 2026-08-16. The
# obfuscated names and the emoji marker are how the tool actually prints.
_DUMP_INCIDENT = """\
fpfs global state
  + supports FPFS: 1
domain: (default) (hidden)
  + FPDDomain instance: <FPDDomain:0x7faeda507730>
domain: 9{34}9 (i{9}e)
  + FPDDomain instance: <FPDDomain:0x7faeda518a80>
      pending-indexable-count: 4379
    + upload progress: <gprogress:NSProgressFileOperationKindUploading url:~/L{5}y/M{14}s>
    + error generation: 628
      i:docID(3373) fetch-content: \U0001f536 last:'1782887470 (-1119h51min)' count:1 error:'NSError: libfssync.FileTreeError itemNotFound'
      i:docID(3371) fetch-content: \U0001f536 last:'1782887470 (-1119h51min)' count:1 error:'NSError: libfssync.FileTreeError itemNotFound'
domain: c{34}b (D{6}x)
  + error generation: 1
      pending-indexable-count: 0
"""


class TestParser:
    def test_parses_the_incident_dump(self) -> None:
        domains = parse_fp_dump(_DUMP_INCIDENT)
        by_name = {d.domain: d for d in domains}
        assert set(by_name) == {"hidden", "i{9}e", "D{6}x"}

        sick = by_name["i{9}e"]
        assert sick.error_generation == 628
        assert sick.pending_indexable == 4379
        assert sick.stuck_errors == 2
        assert sick.level == "fail"

        healthy = by_name["D{6}x"]
        assert healthy.error_generation == 1
        assert healthy.level == "ok"

    def test_hidden_default_domain_without_counters_is_ok(self) -> None:
        """The hidden default domain never prints counters; grading it
        `unknown` forever would make every healthy host nag."""
        domains = parse_fp_dump(_DUMP_INCIDENT)
        hidden = next(d for d in domains if d.domain == "hidden")
        assert hidden.error_generation is None
        assert hidden.level == "ok"

    def test_multiple_generation_lines_keep_the_worst(self) -> None:
        """A domain section can print more than one generation counter; the
        loop is whichever counter is climbing."""
        text = (
            "domain: a (x)\n"
            "  + error generation: 3\n"
            "  + error generation: 150\n"
        )
        (domain,) = parse_fp_dump(text)
        assert domain.error_generation == 150
        assert domain.level == "fail"

    def test_empty_text_yields_no_domains(self) -> None:
        assert parse_fp_dump("") == []


class TestLevels:
    def test_healthy_generation_is_ok(self) -> None:
        assert fp_domain_level(0, thresholds=DEFAULT_THRESHOLDS) == "ok"
        assert fp_domain_level(2, thresholds=DEFAULT_THRESHOLDS) == "ok"

    def test_young_loop_warns(self) -> None:
        """At ~1 generation/hour observed, 10 is a loop hours old — the point
        of the check is catching it then, not on day 46."""
        assert fp_domain_level(10, thresholds=DEFAULT_THRESHOLDS) == "warn"

    def test_established_loop_fails(self) -> None:
        assert fp_domain_level(100, thresholds=DEFAULT_THRESHOLDS) == "fail"
        assert fp_domain_level(628, thresholds=DEFAULT_THRESHOLDS) == "fail"

    def test_unparsed_generation_is_unknown_not_ok(self) -> None:
        assert fp_domain_level(None, thresholds=DEFAULT_THRESHOLDS) == "unknown"


def _domain(name: str, gen: int | None, level: str) -> FPDomainHealth:
    return FPDomainHealth(domain=name, error_generation=gen, level=level)  # type: ignore[arg-type]


class TestCheck:
    def test_not_collected_is_skipped_never_ok(self) -> None:
        """What makes fp_sync_stuck safe in the DEFAULT fail-on set."""
        assert check_fp_sync_stuck(None).level == "skipped"

    def test_empty_parse_is_unknown_not_ok(self) -> None:
        """A parser that silently stopped matching a drifted dump format must
        not read as an estate with no domains."""
        assert check_fp_sync_stuck([]).level == "unknown"

    def test_looping_domain_fails_the_check(self) -> None:
        result = check_fp_sync_stuck([
            _domain("ok-one", 1, "ok"),
            _domain("sick", 628, "fail"),
        ])
        assert result.level == "fail"
        assert "sick" in result.details["domains"]

    def test_all_healthy_is_ok(self) -> None:
        result = check_fp_sync_stuck([_domain("a", 0, "ok"), _domain("b", 2, "ok")])
        assert result.level == "ok"
