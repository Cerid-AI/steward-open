# SPDX-License-Identifier: Apache-2.0

"""FSEvents-backed :class:`steward.core.scanner.watcher.WatcherProtocol`.

This is the macOS implementation. ``watchdog`` selects the FSEvents
adapter automatically on Darwin; on Linux it falls back to inotify
(used in CI). The protocol is the contract — the concrete class is a
thin shim that:

1. Subscribes to a root via ``watchdog.observers.Observer``.
2. Translates ``watchdog`` events into Steward's :class:`FileEvent`.
3. Applies the same skiplist as the scanner so noise events
   (``.DS_Store``, ``@eaDir``, ``.fseventsd``, …) never reach the
   consumer.
4. Debounces bursts: events accumulate in a thread-safe buffer; ``drain``
   returns them after ``debounce_seconds`` of quiet — typical for a
   batch ``rsync`` or ``cp -R`` operation.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

# watchdog ships its own typings but the names live under
# ``watchdog.events`` (FileSystemEventHandler) and
# ``watchdog.observers`` (Observer). Local import inside the class would
# pay the cost every start() call; import at module load is fine since
# the watcher is gated behind the CLI subcommand.
from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from steward.core.scanner.watcher import (
    EventBatch,
    EventKind,
    FileEvent,
    WatcherProtocol,
)
from steward.infra.scanner.skiplist import is_skipped_dir, is_skipped_file

logger = logging.getLogger("steward.infra.scanner.fsevents_watcher")


def _path_is_skipped(path_str: str) -> bool:
    """Apply the scanner's skiplist to a watchdog event path.

    Returns True when any component of the path matches a skip-dir name
    (``.fseventsd``, ``@eaDir``, …) or the basename matches the
    skip-file rules (``.DS_Store``, dotfiles starting with ``._``, …).
    """
    parts = path_str.split("/")
    for part in parts[:-1]:  # any parent directory match disqualifies the event
        if is_skipped_dir(part):
            return True
    basename = parts[-1] if parts else ""
    if basename and is_skipped_file(basename):
        return True
    return False


class _Handler(FileSystemEventHandler):
    """Translate ``watchdog`` events into :class:`FileEvent` and append to a buffer.

    The handler runs on watchdog's emitter thread; it pushes events into
    a list guarded by a lock. The consumer thread reads from that list
    via :meth:`FSEventsWatcher.drain`.
    """

    def __init__(self, buffer: list[FileEvent], lock: threading.Lock, idle_ts: list[float]) -> None:
        super().__init__()
        self._buffer = buffer
        self._lock = lock
        # Single-element list used as a mutable shared "last activity" timestamp.
        self._idle_ts = idle_ts

    def _push(self, event: FileEvent) -> None:
        with self._lock:
            self._buffer.append(event)
            self._idle_ts[0] = time.monotonic()

    # --- watchdog dispatch ------------------------------------------------

    def on_created(self, event: FileSystemEvent) -> None:
        if isinstance(event, DirCreatedEvent):
            return  # directories don't carry content; the walker reaches files via os.walk
        if not isinstance(event, FileCreatedEvent):
            return
        if _path_is_skipped(str(event.src_path)):
            return
        self._push(FileEvent(path=Path(str(event.src_path)), kind=EventKind.CREATED))

    def on_modified(self, event: FileSystemEvent) -> None:
        if isinstance(event, DirModifiedEvent):
            return
        if not isinstance(event, FileModifiedEvent):
            return
        if _path_is_skipped(str(event.src_path)):
            return
        self._push(FileEvent(path=Path(str(event.src_path)), kind=EventKind.MODIFIED))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if isinstance(event, DirDeletedEvent):
            return
        if not isinstance(event, FileDeletedEvent):
            return
        if _path_is_skipped(str(event.src_path)):
            return
        self._push(FileEvent(path=Path(str(event.src_path)), kind=EventKind.DELETED))

    def on_moved(self, event: FileSystemEvent) -> None:
        if isinstance(event, DirMovedEvent):
            return
        if not isinstance(event, FileMovedEvent):
            return
        src = str(event.src_path)
        dest = str(event.dest_path)
        if _path_is_skipped(src) and _path_is_skipped(dest):
            return
        self._push(
            FileEvent(
                path=Path(src),
                kind=EventKind.MOVED,
                moved_to=Path(dest),
            )
        )


class FSEventsWatcher(WatcherProtocol):
    """``watchdog`` Observer wrapped to satisfy :class:`WatcherProtocol`.

    Construct with one or more roots, ``start()`` to subscribe, and call
    ``drain()`` in a loop to harvest debounced batches. The watcher is
    thread-safe: events arrive on watchdog's emitter thread and are
    consumed from the caller's thread under a lock.
    """

    def __init__(
        self,
        roots: list[Path],
        *,
        recursive: bool = True,
        debounce_seconds: float = 0.75,
    ) -> None:
        if not roots:
            raise ValueError("FSEventsWatcher requires at least one root")
        self._roots = [Path(r).resolve() for r in roots]
        self._recursive = recursive
        self._debounce_seconds = debounce_seconds
        self._buffer: list[FileEvent] = []
        self._lock = threading.Lock()
        # Mutable single-cell so the handler can update it without re-binding.
        self._idle_ts: list[float] = [time.monotonic()]
        self._observer = Observer()
        self._handler = _Handler(self._buffer, self._lock, self._idle_ts)
        self._started = False
        self._stopped = False

    # --- WatcherProtocol --------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        for root in self._roots:
            if not root.exists():
                logger.warning(
                    "fsevents_watcher.start.missing-root",
                    extra={"root": str(root)},
                )
                continue
            # watchdog ships its own (partial) typings; these calls land in
            # untyped territory under mypy strict.
            self._observer.schedule(  # type: ignore[no-untyped-call]
                self._handler, str(root), recursive=self._recursive
            )
        self._observer.start()  # type: ignore[no-untyped-call]
        self._started = True

    def stop(self) -> None:
        if not self._started or self._stopped:
            return
        self._stopped = True
        self._observer.stop()  # type: ignore[no-untyped-call]
        self._observer.join(timeout=5.0)

    def drain(self, *, max_wait_seconds: float) -> EventBatch:
        """Return events once the buffer has been quiet for ``debounce_seconds``,
        bounded by ``max_wait_seconds``.

        Returns an empty :class:`EventBatch` if nothing happened during
        the wait — the CLI loop uses that as its tick.
        """
        deadline = time.monotonic() + max_wait_seconds
        while True:
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            with self._lock:
                idle_for = time.monotonic() - self._idle_ts[0]
                has_events = bool(self._buffer)
            now = time.monotonic()
            if has_events and idle_for >= self._debounce_seconds:
                break
            if now >= deadline:
                break

        with self._lock:
            if not self._buffer:
                return EventBatch(events=[])
            events = list(self._buffer)
            self._buffer.clear()
        return EventBatch(events=events)


__all__ = ["FSEventsWatcher"]
