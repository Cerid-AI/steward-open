# SPDX-License-Identifier: Apache-2.0

"""Estate model: hosts, the volumes they mount, and which host owns each volume. Pure."""

from steward.core.estate.legacy import LEGACY_HOST, default_estate
from steward.core.estate.ownership import ACTIONS, Action, Decision, OwnershipPolicy, decide
from steward.core.estate.resolve import HostResolver, VolumeMatch, classify
from steward.core.estate.schema import (
    Access,
    CloudFP,
    Estate,
    Host,
    HostPublish,
    Mount,
    PlanMode,
    PrefixRule,
    ReplicaGrant,
    Volume,
)

__all__ = [
    "ACTIONS",
    "LEGACY_HOST",
    "Access",
    "Action",
    "CloudFP",
    "Decision",
    "Estate",
    "Host",
    "HostPublish",
    "HostResolver",
    "Mount",
    "OwnershipPolicy",
    "PlanMode",
    "PrefixRule",
    "ReplicaGrant",
    "Volume",
    "VolumeMatch",
    "classify",
    "decide",
    "default_estate",
]
