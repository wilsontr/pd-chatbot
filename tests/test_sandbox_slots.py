"""The sandbox run limit holds across processes (TASK-5.34)."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from sandbox import slots
from sandbox.errors import SandboxError
from sandbox.slots import DeploymentSlots, lock_url

TEST_PG = os.environ.get("TEST_DATABASE_URL")


class FakeLocks:
    """Advisory locks as Postgres keeps them: one owner per (namespace, slot)."""

    def __init__(self):
        self.owners: dict[tuple[int, int], int] = {}
        self.guard = threading.Lock()

    def connect(self, url):
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, locks: FakeLocks):
        self.locks = locks
        self.closed = False

    def execute(self, sql, params):
        key = tuple(params)
        with self.locks.guard:
            if "pg_try_advisory_lock" in sql:
                taken = key in self.locks.owners
                if not taken:
                    self.locks.owners[key] = id(self)
                return _Row(not taken)
            if "pg_advisory_unlock" in sql:
                return _Row(self.locks.owners.pop(key, None) == id(self))
        raise AssertionError(sql)

    def close(self):
        # Postgres drops a session's locks with the session.
        with self.locks.guard:
            for key, owner in list(self.locks.owners.items()):
                if owner == id(self):
                    del self.locks.owners[key]
        self.closed = True


class _Row:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class TestLockUrl:
    def test_postgres_urls_are_normalised_and_others_ignored(self, monkeypatch):
        monkeypatch.delenv("PD_SANDBOX_LOCK_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/db")
        assert lock_url() == "postgresql://u:p@h:5432/db"
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/db")
        assert lock_url() == "postgresql://u:p@h/db"
        monkeypatch.setenv("DATABASE_URL", "sqlite:///runs.db")
        assert lock_url() is None
        monkeypatch.setenv("PD_SANDBOX_LOCK_URL", "postgresql://locks/db")
        assert lock_url() == "postgresql://locks/db"


class TestDeploymentSlots:
    def test_holders_beyond_the_limit_wait_then_give_up(self):
        locks = FakeLocks()
        a = DeploymentSlots("pg", 2, connect=locks.connect)
        b = DeploymentSlots("pg", 2, connect=locks.connect)
        c = DeploymentSlots("pg", 2, connect=locks.connect)
        with a.hold(1) as first, b.hold(1) as second:
            assert {first, second} == {0, 1}
            t0 = time.monotonic()
            with pytest.raises(SandboxError, match="busy"):
                with c.hold(0.3):
                    pass
            assert time.monotonic() - t0 >= 0.3
        assert locks.owners == {}

    def test_a_waiting_run_gets_the_slot_once_it_is_freed(self):
        locks = FakeLocks()
        first = DeploymentSlots("pg", 1, connect=locks.connect)
        second = DeploymentSlots("pg", 1, connect=locks.connect)
        release = threading.Event()

        def hold_briefly():
            with first.hold(1):
                release.wait(2)

        holder = threading.Thread(target=hold_briefly)
        holder.start()
        while not locks.owners:
            time.sleep(0.01)
        threading.Timer(0.3, release.set).start()
        with second.hold(3) as slot:
            assert slot == 0
        holder.join()

    def test_an_unreachable_database_falls_back_to_the_process_limit(self, caplog):
        def refuse(url):
            raise OSError("connection refused")

        with DeploymentSlots("pg", 1, connect=refuse).hold(1) as slot:
            assert slot is None
        assert "per process only" in caplog.text


class TestRunSlot:
    def test_without_postgres_only_the_process_limit_applies(self, monkeypatch):
        monkeypatch.delenv("PD_SANDBOX_LOCK_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(slots, "DeploymentSlots", None)  # never reached
        with slots.run_slot(1):
            pass


@pytest.mark.skipif(not TEST_PG, reason="needs TEST_DATABASE_URL (a scratch Postgres)")
class TestAgainstPostgres:
    def _url(self, monkeypatch):
        monkeypatch.setenv("PD_SANDBOX_LOCK_URL", TEST_PG)
        return lock_url()

    def test_two_sessions_contend_for_one_slot(self, monkeypatch):
        url = self._url(monkeypatch)
        with DeploymentSlots(url, 1).hold(1) as slot:
            assert slot == 0
            with pytest.raises(SandboxError, match="busy"):
                with DeploymentSlots(url, 1).hold(0.3):
                    pass
        with DeploymentSlots(url, 1).hold(1) as slot:
            assert slot == 0

    def test_a_killed_process_frees_its_slot(self, tmp_path, monkeypatch):
        url = self._url(monkeypatch)
        ready = tmp_path / "ready"
        repo = Path(__file__).resolve().parent.parent
        script = textwrap.dedent(f"""
            import os, sys, time
            sys.path.insert(0, {str(repo)!r})
            from sandbox.slots import DeploymentSlots
            with DeploymentSlots({url!r}, 1).hold(5):
                open({str(ready)!r}, "w").close()
                time.sleep(30)
        """)
        proc = subprocess.Popen([sys.executable, "-c", script])
        try:
            deadline = time.monotonic() + 10
            while not ready.exists():
                assert time.monotonic() < deadline, "child never took the slot"
                time.sleep(0.05)
            with pytest.raises(SandboxError, match="busy"):
                with DeploymentSlots(url, 1).hold(0.3):
                    pass
            proc.kill()
            proc.wait()
            with DeploymentSlots(url, 1).hold(5) as slot:
                assert slot == 0
        finally:
            proc.kill()
