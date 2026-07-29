# SPDX-License-Identifier: Apache-2.0

"""Watcher protocol — pure interface for incremental filesystem observers.

A *watcher* observes a root and emits :class:`FileEvent` items as the
underlying filesystem changes. v0.2 ships :class:`steward.infra.scanner.fsevents_watcher.FSEventsWatcher`
(macOS fsevents via ``watchdog``); v0.3 will add Linux inotify and the
multi-machine watcher.

Per ADR-0009 (pull-don't-push), a watcher MUST NOT auto-apply policy.
Its job is to keep the inventory fresh — the operator still drives
``steward apply``. The default flow is:

1. Watcher emits a batch of events for files that changed.
2. The orchestrator calls
   :func:`steward.infra.scanner.incremental.scan_paths` to hash the
   affected files and update claims.
3. The operator (or a scheduled job) runs
   ``steward policy plan`` to produce a manifest.
4. ``steward apply --execute`` is the only path that mutates files.

Watchers run in their own thread (or process). The protocol exposes
``start`` / ``stop`` for lifecycle and ``drain`` for cooperative polling
in a CLI loop.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


class EventKind(str, Enum):
    """Why the watcher emitted an event.

    Values are stable strings (not ints) so they survive serialization
    through audit-log payloads.
    """

    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    MOVED = "moved"


@dataclass(frozen=True, slots=True)
class FileEvent:
    """One filesystem event observed by a :class:`WatcherProtocol`.

    Attributes
    ----------
    path:
        Absolute path the event applies to. For :attr:`EventKind.MOVED`
        this is the source path; the destination is in :attr:`moved_to`.
    kind:
        The event class.
    moved_to:
        Destination path for :attr:`EventKind.MOVED` events; ``None``
        for everything else.
    """

    path: Path
    kind: EventKind
    moved_to: Path | None = None

    def all_paths(self) -> tuple[Path, ...]:
        """Return every path this event references (source + dest)."""
        if self.moved_to is not None:
            return (self.path, self.moved_to)
        return (self.path,)


@dataclass
class EventBatch:
    """A debounced collection of file events flushed together.

    The watcher coalesces a burst of events (typical of editor saves,
    rsync runs, etc.) into one batch so the consumer can deduplicate
    paths and run a single incremental scan over them.
    """

    events: list[FileEvent] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.events)

    def is_empty(self) -> bool:
        return not self.events

    def unique_paths(self, *, drop_deleted: bool = True) -> tuple[Path, ...]:
        """Return deduplicated paths from the batch, source-first.

        ``drop_deleted`` excludes paths for :attr:`EventKind.DELETED`
        events — they no longer exist on disk and the scanner would
        just log an OSError. The default is True because the scanner
        is what populates / refreshes claims; deletions are reflected
        by a subsequent ``is_current = 0`` sweep, not by re-walking.
        """
        seen: set[Path] = set()
        ordered: list[Path] = []
        for ev in self.events:
            if drop_deleted and ev.kind is EventKind.DELETED:
                continue
            for p in ev.all_paths():
                if p in seen:
                    continue
                seen.add(p)
                ordered.append(p)
        return tuple(ordered)

    def extend(self, events: Iterable[FileEvent]) -> None:
        self.events.extend(events)


@runtime_checkable
class WatcherProtocol(Protocol):
    """Lifecycle for a filesystem watcher.

    Implementations live under ``steward.infra.scanner`` so they can
    depend on the OS-specific bindings (``watchdog`` for fsevents,
    inotify for Linux). The CLI talks only to this protocol.

    A watcher is single-use: ``start`` once, then loop on ``drain``
    until the operator stops the process; ``stop`` is idempotent.
    """

    def start(self) -> None:
        """Begin observing the configured roots. Non-blocking."""
        ...

    def stop(self) -> None:
        """Stop observing. Safe to call multiple times."""
        ...

    def drain(self, *, max_wait_seconds: float) -> EventBatch:
        """Wait up to ``max_wait_seconds`` and return the current batch.

        Returns an :class:`EventBatch` (possibly empty) after either the
        debounce window elapses or ``max_wait_seconds`` passes — whichever
        comes first. An empty batch is the normal idle case.
        """
        ...


__all__ = [
    "EventBatch",
    "EventKind",
    "FileEvent",
    "WatcherProtocol",
]
