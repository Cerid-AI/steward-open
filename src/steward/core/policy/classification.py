# SPDX-License-Identifier: Apache-2.0

"""Claim-level domain + cluster classifier.

Operates on already-ingested claims (ADR-0010 — classification is deferred
from ingest). The path is the only signal; mtime / size are ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from steward.core.policy.schema import ClassificationPolicy


@dataclass(frozen=True)
class Classification:
    """Result of classifying one path."""

    domain: str | None
    cluster: str | None


def _domain_for_path(path: str, policy: ClassificationPolicy) -> str | None:
    """Return the first matching domain name, or None if no domain matched.

    Order is preserved: the first matching rule wins. Operators authoring
    a policy edit ``domains:`` order to express precedence.
    """
    lowered = path.lower()
    for domain in policy.domains:
        for rule in domain.rules:
            for needle in rule.path_substring_any_of:
                if needle.lower() in lowered:
                    return domain.name
    return None


def _cluster_for_path(path: str, policy: ClassificationPolicy) -> str | None:
    """Return the first matching cluster label, or None.

    Cluster regexes are compiled once per classify-run via
    :func:`compile_cluster_regexes`. This helper is the
    pre-compile path used by tests; production code uses the cached
    classifier below.
    """
    for entry in policy.clusters:
        for raw in entry.regex_any_of:
            if re.search(raw, path):
                return entry.label
    return None


class Classifier:
    """Pre-compiled classifier for hot-path use.

    Compiling cluster regexes once + reusing for every claim is the
    difference between "fast classify pass" and "classify pass that's
    slower than re-scanning the FS".
    """

    def __init__(self, policy: ClassificationPolicy) -> None:
        self._policy = policy
        self._cluster_compiled: list[tuple[str, list[re.Pattern[str]]]] = [
            (entry.label, [re.compile(p) for p in entry.regex_any_of])
            for entry in policy.clusters
        ]

    def classify(self, path: str) -> Classification:
        domain = _domain_for_path(path, self._policy)
        cluster: str | None = None
        for label, patterns in self._cluster_compiled:
            if any(p.search(path) for p in patterns):
                cluster = label
                break
        return Classification(domain=domain, cluster=cluster)
