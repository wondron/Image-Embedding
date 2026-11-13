"""The gate in front of vector writes to the current collection, so a model version switch never
loses an image.

Writers that put new vectors into the current collection (index, re-embed, the reconciler filling
missing vectors) hold it shared while they take the stack and write. A switch holds it
exclusively: new writers wait, the ones in flight finish, then the switch embeds what the target
version still lacks and swaps the stack in, all before any writer can reach the old collection
again. Writers only wait while a switch runs (seconds), never on each other.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from app.core import errors as err

# How long a writer waits for a switch to finish, and a switch for the writes in flight
WRITE_WAIT_SECONDS = 120.0


class VectorWriteGate:
    def __init__(self, wait_seconds: float = WRITE_WAIT_SECONDS) -> None:
        self._cond = threading.Condition()
        self._writers = 0
        self._closed = False
        self._wait = wait_seconds

    @property
    def closed(self) -> bool:
        return self._closed

    @contextmanager
    def shared(self) -> Iterator[None]:
        """One write; waits while a switch runs (NOT_READY if it does not end in time)."""
        with self._cond:
            if not self._cond.wait_for(lambda: not self._closed, timeout=self._wait):
                raise err.not_ready("a model version switch is in progress; try again shortly")
            self._writers += 1
        try:
            yield
        finally:
            with self._cond:
                self._writers -= 1
                self._cond.notify_all()

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """A switch: no writer runs inside. NOT_READY when the writes in flight do not end in time
        (nothing was switched; try again)."""
        with self._cond:
            if not self._cond.wait_for(lambda: not self._closed, timeout=self._wait):
                raise err.not_ready("another model version switch is in progress")
            self._closed = True
            if not self._cond.wait_for(lambda: self._writers == 0, timeout=self._wait):
                self._closed = False
                self._cond.notify_all()
                raise err.not_ready("vector writes in flight did not finish in time; try the switch again")
        try:
            yield
        finally:
            with self._cond:
                self._closed = False
                self._cond.notify_all()
