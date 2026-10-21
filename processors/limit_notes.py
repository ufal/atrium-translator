"""processors/limit_notes.py — where a limit that shaped a result writes its note (#53).

``atrium_limits.LimitNotes`` records every limit that shaped a translation without refusing
it (``limits_applied`` in the paradata and in the service response). The notes are made
deep inside the processors and the backends — where a segment is sampled for language
identification, split into chunks, or given up on — and the backends are ONE shared
object per process, so the notes cannot live on them.

``main.process_single_file`` therefore opens a collector for the file it processes
(:func:`collecting`) and the code below it adds to it with :func:`note`. The collector is
thread-local, which is sound here and only here because ``process_single_file`` is
synchronous and single-threaded: the batch CLI calls it in a loop, and the service runs
each call in ONE worker thread (``asyncio.to_thread``), and nothing inside it starts a
thread. Outside a collector, :func:`note` does nothing, so a unit test or a direct call
of a backend needs no setup.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional

from atrium_limits import LimitNotes, LimitSpec

_active = threading.local()


@contextmanager
def collecting(notes: Optional[LimitNotes] = None) -> Iterator[LimitNotes]:
    """Collect the notes made in this thread until the block exits; yields the collector."""
    collector = notes if notes is not None else LimitNotes()
    previous = getattr(_active, "notes", None), getattr(_active, "seen", None)
    _active.notes, _active.seen = collector, set()
    try:
        yield collector
    finally:
        _active.notes, _active.seen = previous


def note(spec: LimitSpec, effect: str, count: int = 1, detail: str = "", *, key: Optional[str] = None) -> None:
    """Record that ``spec`` shaped the result, if a collector is open in this thread.

    With *key* (e.g. the segment's text), a second note for the same limit, effect and key
    within one collector is dropped: the end-of-document re-run sends a segment again, and
    it is still one segment that the limit shaped.
    """
    collector: Optional[LimitNotes] = getattr(_active, "notes", None)
    if collector is None:
        return
    if key is not None:
        seen = _active.seen
        marker = (spec.key, effect, key)
        if marker in seen:
            return
        seen.add(marker)
    collector.note(spec, effect, count, detail)
