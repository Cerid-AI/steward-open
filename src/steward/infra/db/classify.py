# SPDX-License-Identifier: Apache-2.0

"""Classify-pass facade — load policy, walk claims, update domain + classification."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from steward.core.policy import load_policy
from steward.core.policy.classification import Classifier
from steward.core.policy.schema import ClassificationPolicy
from steward.infra.db import repo_audit
from steward.infra.db.admin import resolve_machine_id
from steward.infra.db.connect import connect
from steward.infra.db.settings import inventory_db_path

logger = logging.getLogger("steward.infra.db.classify")


@dataclass(frozen=True)
class ClassifyResult:
    claims_scanned: int
    domain_updated: int
    classification_updated: int
    reclassify_all: bool


def classify_claims(
    *,
    policy_path: Path,
    reclassify_all: bool = False,
    db_path: Path | None = None,
) -> ClassifyResult:
    """Walk claims and assign domain + classification labels.

    By default, only claims with ``domain IS NULL`` or
    ``classification IS NULL`` are touched. ``reclassify_all=True``
    re-classifies every claim (use after editing classification.yml).
    """
    target = (db_path or inventory_db_path()).expanduser()
    policy = load_policy(policy_path)
    if not isinstance(policy, ClassificationPolicy):
        raise TypeError(f"classify_claims requires a ClassificationPolicy YAML; got {type(policy).__name__}")
    classifier = Classifier(policy)
    machine_id = resolve_machine_id(target)

    con = connect(target)
    try:
        if reclassify_all:
            cur = con.execute("SELECT id, file_path FROM claims")
        else:
            cur = con.execute("SELECT id, file_path FROM claims WHERE domain IS NULL OR classification IS NULL")

        rows = list(cur)
        domain_updated = 0
        cluster_updated = 0
        for claim_id, file_path in rows:
            result = classifier.classify(str(file_path))
            if result.domain is not None:
                cur = con.execute(
                    "UPDATE claims SET domain = ? WHERE id = ? AND (domain IS NULL OR ? = 1)",
                    (result.domain, claim_id, 1 if reclassify_all else 0),
                )
                if cur.rowcount > 0:
                    domain_updated += 1
            if result.cluster is not None:
                cur = con.execute(
                    "UPDATE claims SET classification = ? WHERE id = ? AND (classification IS NULL OR ? = 1)",
                    (result.cluster, claim_id, 1 if reclassify_all else 0),
                )
                if cur.rowcount > 0:
                    cluster_updated += 1

        repo_audit.append(
            con,
            machine_id=machine_id,
            actor="steward-classify",
            action="reclassify",
            payload={
                "policy_path": str(policy_path),
                "claims_scanned": len(rows),
                "domain_updated": domain_updated,
                "classification_updated": cluster_updated,
                "reclassify_all": reclassify_all,
            },
        )
        con.commit()
    finally:
        con.close()

    return ClassifyResult(
        claims_scanned=len(rows),
        domain_updated=domain_updated,
        classification_updated=cluster_updated,
        reclassify_all=reclassify_all,
    )
