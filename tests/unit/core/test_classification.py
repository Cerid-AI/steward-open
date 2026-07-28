# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the path classifier."""
from __future__ import annotations

import pytest

from steward.core.policy.classification import Classifier
from steward.core.policy.schema import (
    ClassificationPolicy,
    ClusterEntry,
    DomainEntry,
    DomainRule,
)


@pytest.fixture
def policy() -> ClassificationPolicy:
    return ClassificationPolicy(
        version=1,
        kind="ClassificationPolicy",
        domains=[
            DomainEntry(
                name="photos",
                rules=[DomainRule(path_substring_any_of=[".photoslibrary/", "/dcim/"])],
            ),
            DomainEntry(
                name="music",
                rules=[DomainRule(path_substring_any_of=["/music/", ".musiclibrary/"])],
            ),
        ],
        clusters=[
            ClusterEntry(label="Trash", regex_any_of=[r"Recently Deleted", r"\$RECYCLE"]),
        ],
    )


def test_domain_match(policy: ClassificationPolicy) -> None:
    c = Classifier(policy)
    assert c.classify("/home/operator/Photos/Photos.photoslibrary/foo").domain == "photos"
    assert c.classify("/Volumes/Backup/DCIM/100MEDIA").domain == "photos"
    assert c.classify("/home/operator/Music/Album/track.mp3").domain == "music"
    assert c.classify("/home/operator/Documents/x.pdf").domain is None


def test_cluster_match(policy: ClassificationPolicy) -> None:
    c = Classifier(policy)
    assert c.classify("/Volumes/Backup/Recently Deleted/x.jpg").cluster == "Trash"
    assert c.classify("/Volumes/Backup/$RECYCLE.BIN/x").cluster == "Trash"
    assert c.classify("/anywhere/else").cluster is None


def test_first_match_wins(policy: ClassificationPolicy) -> None:
    """When multiple domain rules could match, the FIRST domain in YAML order wins."""
    c = Classifier(policy)
    # Path contains both photoslibrary AND music — photos comes first → photos.
    assert c.classify("/home/operator/.photoslibrary/Music").domain == "photos"


def test_case_insensitive(policy: ClassificationPolicy) -> None:
    c = Classifier(policy)
    # The classifier lowercases the haystack so "/MUSIC/" matches "/music/".
    assert c.classify("/home/operator/MUSIC/album.mp3").domain == "music"
