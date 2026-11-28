"""How many sandbox runs may happen at once, across a whole deployment (TASK-5.34).

``PD_SANDBOX_CONCURRENCY`` is the number of runs at once. Every run holds a slot
in this process, so one process never goes past it. When a Postgres database is
configured (``PD_SANDBOX_LOCK_URL``, else ``DATABASE_URL``), a run also holds one
of that many Postgres session advisory locks, so the same number holds across
every worker process and replica that shares the database — which is what bounds
Modal spend. A lock belongs to its connection, so a process that dies mid-run
frees its slot as soon as Postgres notices the connection has gone.

If Postgres cannot be reached the run goes ahead under the per-process limit
alone, with a warning: an outage of the lock database should not stop patches
running, and the per-process limit still bounds each process.
"""

from __future__ import annotations

import contextlib
import logging
import os
import random
import threading
import time
from typing import Any, Callable, Iterator

from sandbox.errors import SandboxError

logger = logging.getLogger(__name__)

#: First key of the two-int advisory lock; the second is the slot number.
#: ASCII "PdSb", so the locks are recognisable in ``pg_locks``.
LOCK_NAMESPACE = 0x50645362
_POLL_SECONDS = 0.2
_CONNECT_TIMEOUT = 5


def concurrency() -> int:
    return max(1, int(os.environ.get("PD_SANDBOX_CONCURRENCY", "2")))


def lock_url() -> str | None:
    """The Postgres URL shared by the deployment, as psycopg wants it, or None."""
    raw = os.environ.get("PD_SANDBOX_LOCK_URL") or os.environ.get("DATABASE_URL") or ""
    for prefix in ("postgresql+psycopg://", "postgres://"):
        if raw.startswith(prefix):
            return "postgresql://" + raw[len(prefix):]
    return raw if raw.startswith("postgresql://") else None


def _connect(url: str) -> Any:
    import psycopg

    return psycopg.connect(url, autocommit=True, connect_timeout=_CONNECT_TIMEOUT)


class DeploymentSlots:
    """``size`` slots shared by every process that uses the same database."""

    def __init__(
        self, url: str, size: int, *, connect: Callable[[str], Any] = _connect
    ):
        self.url = url
        self.size = size
        self._connect = connect

    @contextlib.contextmanager
    def hold(self, timeout: float) -> Iterator[int | None]:
        """Hold one slot for the block; its number, or None if Postgres was unreachable."""
        try:
            conn = self._connect(self.url)
        except Exception as exc:
            logger.warning(
                "sandbox lock database unreachable, limiting runs per process only: %s", exc
            )
            yield None
            return
        try:
            slot = self._take(conn, timeout)
            try:
                yield slot
            finally:
                try:
                    conn.execute(
                        "SELECT pg_advisory_unlock(%s, %s)", (LOCK_NAMESPACE, slot)
                    )
                except Exception:
                    logger.warning("failed to release sandbox slot %d", slot, exc_info=True)
        finally:
            try:
                conn.close()  # also releases the lock if the unlock failed
            except Exception:
                pass

    def _take(self, conn: Any, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        # Start at a random slot so waiting processes do not all poll slot 0 first.
        start = random.randrange(self.size)
        while True:
            for i in range(self.size):
                slot = (start + i) % self.size
                row = conn.execute(
                    "SELECT pg_try_advisory_lock(%s, %s)", (LOCK_NAMESPACE, slot)
                ).fetchone()
                if row and row[0]:
                    return slot
            if time.monotonic() >= deadline:
                raise SandboxError("sandbox is busy")
            time.sleep(_POLL_SECONDS)


_local = threading.BoundedSemaphore(concurrency())


@contextlib.contextmanager
def run_slot(timeout: float) -> Iterator[None]:
    """Hold a slot for one sandbox run, or raise ``SandboxError`` once ``timeout`` passes."""
    started = time.monotonic()
    if not _local.acquire(timeout=timeout):
        raise SandboxError("sandbox is busy")
    try:
        url = lock_url()
        if url is None:
            yield
            return
        remaining = max(0.0, timeout - (time.monotonic() - started))
        with DeploymentSlots(url, concurrency()).hold(remaining):
            yield
    finally:
        _local.release()
