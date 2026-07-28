# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the watcher protocol value objects."""
from __future__ import annotations

from pathlib import Path

from steward.core.scanner.watcher import (
    EventBatch,
    EventKind,
    FileEvent,
    WatcherProtocol,
)


def test_event_kind_values_are_stable_strings() -> None:
    # Stable string values so audit payloads survive JSON round-trips.
    assert EventKind.CREATED.value == "created"
    assert EventKind.MODIFIED.value == "modified"
    assert EventKind.DELETED.value == "deleted"
    assert EventKind.MOVED.value == "moved"


def test_file_event_all_paths_includes_dest_on_moved() -> None:
    ev = FileEvent(
        path=Path("/a/b.txt"),
        kind=EventKind.MOVED,
        moved_to=Path("/a/c.txt"),
    )
    assert ev.all_paths() == (Path("/a/b.txt"), Path("/a/c.txt"))


def test_file_event_all_paths_single_for_non_moved() -> None:
    for kind in (EventKind.CREATED, EventKind.MODIFIED, EventKind.DELETED):
        ev = FileEvent(path=Path("/a/b"), kind=kind)
        assert ev.all_paths() == (Path("/a/b"),)


def test_event_batch_unique_paths_dedups_and_preserves_order() -> None:
    b = EventBatch(
        events=[
            FileEvent(path=Path("/x/1"), kind=EventKind.CREATED),
            FileEvent(path=Path("/x/2"), kind=EventKind.MODIFIED),
            FileEvent(path=Path("/x/1"), kind=EventKind.MODIFIED),  # duplicate
        ]
    )
    assert b.unique_paths() == (Path("/x/1"), Path("/x/2"))


def test_event_batch_drop_deleted_by_default() -> None:
    b = EventBatch(
        events=[
            FileEvent(path=Path("/x/keep"), kind=EventKind.MODIFIED),
            FileEvent(path=Path("/x/gone"), kind=EventKind.DELETED),
        ]
    )
    # Default drops the deleted path — scanner wouldn't find it anyway.
    assert b.unique_paths() == (Path("/x/keep"),)
    # Opt-in to include deleted (e.g. for the bookkeeping pass).
    assert b.unique_paths(drop_deleted=False) == (
        Path("/x/keep"),
        Path("/x/gone"),
    )


def test_event_batch_moved_dest_included() -> None:
    b = EventBatch(
        events=[
            FileEvent(
                path=Path("/x/old"),
                kind=EventKind.MOVED,
                moved_to=Path("/x/new"),
            ),
        ]
    )
    assert b.unique_paths() == (Path("/x/old"), Path("/x/new"))


def test_event_batch_is_empty_and_len() -> None:
    b = EventBatch()
    assert b.is_empty()
    assert len(b) == 0
    b.extend([FileEvent(path=Path("/x"), kind=EventKind.CREATED)])
    assert not b.is_empty()
    assert len(b) == 1


def test_protocol_runtime_check() -> None:
    """A minimal class with the right methods satisfies the Protocol at runtime."""

    class _Toy:
        def start(self) -> None: ...
        def stop(self) -> None: ...
        def drain(self, *, max_wait_seconds: float) -> EventBatch:
            del max_wait_seconds
            return EventBatch()

    assert isinstance(_Toy(), WatcherProtocol)
