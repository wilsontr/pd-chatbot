"""Tests for the headless-Pd sandbox (TASK-5.4).

Container tests need the image (docker build -t pd-sandbox sandbox/). They are
skipped without it unless PD_SANDBOX_REQUIRED is set, as it is in CI. Set
PD_SANDBOX_BACKEND=modal to run them on Modal instead.
"""

import array
import io
import json
import math
import os
import shutil
import subprocess
import sys
import time
import wave

import pytest

from sandbox import SandboxError, run_patch, runner
from tests.needs_sandbox import IMAGE, MODAL, needs_sandbox

needs_image = needs_sandbox


def patch(*boxes, wires=()):
    lines = ["#N canvas 0 0 400 300 12;"]
    lines += [f"#X {kind} 10 {10 + 20 * i} {text};" for i, (kind, text) in enumerate(boxes)]
    lines += [f"#X connect {a} {ao} {b} {bi};" for a, ao, b, bi in wires]
    return "\n".join(lines) + "\n"


def on_load(*messages):
    """A patch that sends global messages when it loads."""
    text = " ".join(rf"\; {m}" for m in messages)
    return patch(("obj", "loadbang"), ("msg", text), wires=[(0, 0, 1, 0)])


TONE = patch(
    ("obj", "osc~ 440"), ("obj", "*~ 0.5"), ("obj", "dac~"),
    wires=[(0, 0, 1, 0), (1, 0, 2, 0), (1, 0, 2, 1)],
)
HANG = patch(
    ("obj", "loadbang"), ("obj", "until"), wires=[(0, 0, 1, 0)],
)


def samples(wav):
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, 48000)
        return array.array("h", w.readframes(w.getnframes()))


# --- Host side, no Docker needed -------------------------------------------


def frame(console=b"", pcm=b"", **over):
    header = {
        "status": "ok", "exit_code": 0, "signal": 0, "console_bytes": len(console),
        "console_truncated": False, "frames": len(pcm) // 4, "peak": 0.5,
        "nonfinite_samples": 0, **over,
    }
    return json.dumps(header).encode() + b"\n" + console + pcm


def test_parse_result():
    r = runner._parse(frame(b"hi\xff\n", bytes(400)), 100)
    assert r.ok and r.console == "hi�\n" and len(samples(r.wav)) == 200


@pytest.mark.parametrize("data", [
    b"", b"no newline", b"[]\n", b"null\n", b"{}\n", b"x" * 10000 + b"\n",
    b"[" * 4000 + b"\n",
    frame(status="made_up"),
    frame(status=["ok"]),
    frame(status={}),
    frame(exit_code="0"),
    frame(exit_code=True),
    frame(frames=-1),
    frame(frames=10**9),
    frame(console_bytes=10**9),
    frame(peak=float("nan")),
    frame(console_truncated=1),
    frame(b"abc", console_bytes=2),
    frame(pcm=bytes(404)),
])
def test_malformed_output_is_rejected(data):
    with pytest.raises(SandboxError):
        runner._parse(data, 100)


@pytest.mark.parametrize("args", [
    (TONE, 0), (TONE, -1), (TONE, 31), (TONE, float("nan")), (TONE, 1, 0), (TONE, 1, 61),
    ("x" * (runner.MAX_PATCH_BYTES + 1), 1),
])
def test_bad_arguments(args):
    with pytest.raises(ValueError):
        run_patch(*args)


def test_command_has_every_restriction(monkeypatch):
    monkeypatch.setattr(runner, "_docker", lambda: "docker")
    monkeypatch.setenv("PD_SANDBOX_RUNTIME", "runsc")
    cmd = runner._command("n", 1, 2)
    for flag in (
        "--network=none", "--read-only", "--cap-drop=ALL", "--ipc=none", "--cpus=1",
        "--security-opt=no-new-privileges", "--pids-limit=32", "--user=65532:65532",
        "--memory=256m", "--memory-swap=256m", "--log-driver=none", "--runtime=runsc",
    ):
        assert flag in cmd
    assert not any(a in ("-v", "--volume", "--mount", "--privileged") for a in cmd)
    assert not any(a.startswith(("--volume=", "--mount=", "--env")) for a in cmd)


def test_host_stops_output_over_the_limit():
    cmd = [sys.executable, "-c", "import sys\nwhile 1: sys.stdout.write('x'*65536)"]
    with pytest.raises(SandboxError, match="too large"):
        runner._run(cmd, "none", b"", 100_000, 20)


def test_host_stops_a_container_that_never_returns():
    start = time.monotonic()
    with pytest.raises(SandboxError, match="in time"):
        runner._run([sys.executable, "-c", "import time; time.sleep(60)"], "none", b"", 100, 1)
    assert time.monotonic() - start < 15


def test_missing_docker(monkeypatch):
    monkeypatch.setenv("PD_SANDBOX_BACKEND", "docker")
    monkeypatch.setenv("PD_SANDBOX_DOCKER", "/nonexistent/docker")
    with pytest.raises(SandboxError, match="could not start"):
        run_patch(TONE, 1)


def write_file(path):
    return patch(
        ("obj", "loadbang"), ("obj", "t b b"), ("msg", f"write {path}"), ("msg", "add hi"),
        ("obj", "textfile"),
        wires=[(0, 0, 1, 0), (1, 1, 3, 0), (1, 0, 2, 0), (3, 0, 4, 0), (2, 0, 4, 0)])


def read_file(path):
    """Prints the file's bytes as numbers; a symbol would be cut at 1000 characters."""
    return patch(
        ("obj", "loadbang"), ("obj", "t b b"), ("msg", "4000"), ("msg", f"open {path}"),
        ("obj", "file handle"), ("obj", "print"),
        wires=[(0, 0, 1, 0), (1, 1, 3, 0), (1, 0, 2, 0), (3, 0, 4, 0), (2, 0, 4, 0),
               (4, 0, 5, 0)])


# --- Container -------------------------------------------------------------


@pytest.fixture
def no_leaks():
    yield
    if MODAL:
        import modal

        from sandbox.modal_backend import _app_and_image
        app, _ = _app_and_image()
        for _ in range(20):  # termination is asynchronous
            left = list(modal.Sandbox.list(app_id=app.app_id))
            if not left:
                return
            time.sleep(0.5)
        raise AssertionError(left)
    out = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=pd-sandbox=1"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out.strip() == ""


container = pytest.mark.usefixtures("no_leaks")


def test_missing_image_is_a_sandbox_error(monkeypatch):
    monkeypatch.setenv("PD_SANDBOX_BACKEND", "docker")
    if not shutil.which("docker"):
        pytest.skip("no docker")
    monkeypatch.setenv("PD_SANDBOX_IMAGE", "pd-sandbox-does-not-exist:none")
    with pytest.raises(SandboxError, match="sandbox failed"):
        run_patch(TONE, 1)


@needs_image
@container
class TestContainer:
    def test_tone(self):
        r = run_patch(TONE, 1.0)
        assert r.ok and r.exit_code == 0 and r.console == ""
        left = samples(r.wav)[::2]
        assert len(left) == 48000 and r.seconds_rendered == 1.0
        rms = math.sqrt(sum(s * s for s in left) / len(left)) / 32767
        assert rms == pytest.approx(0.5 / math.sqrt(2), abs=0.01)
        crossings = sum(a < 0 <= b for a, b in zip(left, left[1:]))
        assert crossings == pytest.approx(440, abs=2)

    def test_duration_is_exact(self):
        assert len(samples(run_patch(TONE, 0.1001).wav)) == 2 * math.ceil(0.1001 * 48000)

    def test_longest_render_fits(self):
        r = run_patch(TONE, 30)
        assert r.ok and r.seconds_rendered == 30

    def test_unknown_object_is_reported(self):
        r = run_patch(patch(("obj", "nosuch~ 3")), 0.1)
        assert r.ok and "nosuch~ 3" in r.console and "couldn't create" in r.console

    def test_print_reaches_console(self):
        r = run_patch(patch(
            ("obj", "loadbang"), ("msg", "hello"), ("obj", "print"),
            wires=[(0, 0, 1, 0), (1, 0, 2, 0)]), 0.1)
        assert "print: hello" in r.console

    def test_extras_load(self):
        r = run_patch(patch(("obj", "sigmund~"), ("obj", "bonk~"), ("obj", "expr~ $v1")), 0.1)
        assert r.ok and "couldn't create" not in r.console

    def test_empty_canvas_is_silent(self):
        r = run_patch(patch(), 0.1)
        assert r.ok and r.peak == 0 and not any(samples(r.wav))

    def test_empty_file_fails_to_load(self):
        r = run_patch("", 0.1)
        assert r.status == "load_failed" and r.exit_code != 0

    def test_garbage_input(self):
        r = run_patch(bytes(range(256)) * 50, 0.1)
        assert r.status in runner._STATUSES

    def test_clipping_and_nan_are_reported(self):
        loud = patch(("obj", "osc~ 440"), ("obj", "*~ 4"), ("obj", "dac~"),
                     wires=[(0, 0, 1, 0), (1, 0, 2, 0)])
        r = run_patch(loud, 0.2)
        assert r.ok and r.peak == pytest.approx(4, abs=0.01) and max(samples(r.wav)) == 32767
        nan = patch(("obj", "sig~ -1e+38"), ("obj", "*~ 1e+38"), ("obj", "*~ 0"), ("obj", "dac~"),
                    wires=[(0, 0, 1, 0), (1, 0, 2, 0), (2, 0, 3, 0)])
        assert run_patch(nan, 0.2).nonfinite_samples > 0

    # Acceptance criterion 3: runaway patches.

    def test_message_loop_times_out(self):
        start = time.monotonic()
        r = run_patch(HANG, 1, wall_seconds=2)
        assert r.status == "timeout" and r.signal == 9
        assert time.monotonic() - start < 12

    def test_loop_during_dsp_keeps_the_audio_so_far(self):
        r = run_patch(patch(
            ("obj", "osc~ 440"), ("obj", "dac~"), ("obj", "loadbang"), ("obj", "delay 500"),
            ("obj", "until"),
            wires=[(0, 0, 1, 0), (2, 0, 3, 0), (3, 0, 4, 0)]), 2, wall_seconds=2)
        assert r.status == "timeout"
        assert 0.4 < r.seconds_rendered < 0.6 and r.peak > 0.9

    def test_memory_hog_is_killed(self):
        n = 8  # 64 MB each: every allocation is plausible, the total is not
        fill = " ".join(rf"\; t{i} const 1" for i in range(n))
        r = run_patch(patch(
            *[("obj", f"table t{i} 8000000") for i in range(n)], ("obj", "loadbang"),
            ("msg", fill), wires=[(n, 0, n + 1, 0)]), 1)
        assert r.status == "memory_limit" and r.signal == 9

    def test_one_huge_allocation_is_killed_or_refused(self):
        r = run_patch(patch(
            ("obj", "table big 200000000"), ("obj", "loadbang"), ("msg", r"\; big const 1"),
            wires=[(1, 0, 2, 0)]), 1)
        # A VM with little memory refuses it; a container is killed when it is used.
        assert r.status == "memory_limit" or "out of memory" in r.console

    @pytest.mark.skipif(MODAL, reason="checks the Docker image directly")
    def test_root_without_hardening_is_refused(self):
        p = subprocess.run(
            ["docker", "run", "--rm", "-i", "--user=0", "--network=none", IMAGE, "1", "2"],
            input=TONE.encode(), capture_output=True, timeout=60)
        assert p.returncode == 70 and p.stdout == b"" and b"unshare" in p.stderr

    def test_large_file_write_is_stopped(self):
        # 18 MB of text in small writes: the kernel signals the first write past 16 MB.
        r = run_patch(patch(
            ("obj", "table big 9000000"), ("obj", "loadbang"),
            ("msg", r"\; big write /work/a.txt"), wires=[(1, 0, 2, 0)]), 1)
        assert r.status == "file_size_limit"

    def test_single_oversized_write_is_cut_short(self):
        # Pd sees a short write at exactly 16 MB, reports it and carries on.
        r = run_patch(patch(
            ("obj", "table big 8000000"), ("obj", "loadbang"),
            ("msg", "write -wave -bytes 3 /work/a.wav big"), ("obj", "soundfiler"),
            wires=[(1, 0, 2, 0), (2, 0, 3, 0)]), 1)
        assert r.ok and "5592390 out of 8000000 frames" in r.console

    def test_many_files_fill_only_the_tmpfs(self):
        writes = r" \, ".join(f"write -wave /work/{i}.wav big" for i in range(5))
        r = run_patch(patch(
            ("obj", "table big 6000000"), ("obj", "loadbang"), ("msg", writes),
            ("obj", "soundfiler"), wires=[(1, 0, 2, 0), (2, 0, 3, 0)]), 1)
        assert r.ok and "No space left" in r.console
        assert run_patch(TONE, 0.1).ok

    def test_console_flood_is_truncated(self):
        r = run_patch(patch(
            ("obj", "loadbang"), ("obj", "until"), ("msg", "x" * 500), ("obj", "print"),
            wires=[(0, 0, 1, 0), (1, 0, 2, 0), (2, 0, 3, 0)]), 1, wall_seconds=2)
        assert r.status == "timeout" and r.console_truncated
        assert len(r.console.encode()) <= runner.MAX_CONSOLE_BYTES

    def test_stack_overflow_is_a_crash_or_pd_error(self):
        r = run_patch(patch(
            ("obj", "loadbang"), ("obj", "t b"), wires=[(0, 0, 1, 0), (1, 0, 1, 0)]), 0.2)
        assert r.status == "crashed" or "stack overflow" in r.console

    def test_thread_bomb_is_contained(self):
        r = run_patch(patch(*[("obj", "readsf~")] * 200), 0.2)
        assert r.status in {"ok", "crashed", "timeout", "patch_exited"}
        assert run_patch(TONE, 0.1).ok

    @pytest.mark.parametrize("message", ["pd quit", "pd quit 125", "pd exit"])
    def test_quitting_is_not_success(self, message):
        r = run_patch(on_load(message), 0.5)
        # Pd's scheduled exit is ignored by libpd, so the run just completes.
        assert r.status == ("ok" if message == "pd exit" else "patch_exited")

    # Acceptance criterion 2: isolation.

    def test_no_network(self):
        r = run_patch(patch(
            ("obj", "loadbang"), ("msg", "connect 1.1.1.1 53"), ("obj", "netsend"),
            wires=[(0, 0, 1, 0), (1, 0, 2, 0)]), 0.5)
        assert "unreachable" in r.console.lower(), r.console

    def test_root_filesystem_is_read_only(self):
        assert run_patch(write_file("/work/ok"), 0.1).console == ""  # control
        for path in ("/evil", "/tmp/evil", "/home/nonroot/evil", "/dev/shm/evil", "/etc/evil"):
            assert "write failed" in run_patch(write_file(path), 0.1).console, path

    def test_nothing_survives_between_runs(self):
        assert run_patch(write_file("/work/left"), 0.1).console == ""
        assert "no such file" in run_patch(read_file("/work/left"), 0.1).console.lower()

    def test_kernel_limits_as_seen_from_inside(self):
        """The flags must take effect, not just be present."""
        if os.environ.get("PD_SANDBOX_RUNTIME"):
            pytest.skip("gVisor's /proc differs")

        def read(path):
            text = run_patch(read_file(path), 0.1).console
            if not text.startswith("print: "):
                return text
            values = text.splitlines()[0].removeprefix("print: ").split()
            return " ".join(bytes(int(v) for v in values).decode().split())

        status = read("/proc/self/status")
        for line in (
            "Uid: 65532 65532 65532 65532", "CapPrm: 0000000000000000",
            "CapEff: 0000000000000000", "CapBnd: 0000000000000000", "NoNewPrivs: 1", "Seccomp: 2",
        ):
            assert line in status
        limits = read("/proc/self/limits")
        assert "Max file size 16777216 16777216" in limits
        assert "Max core file size 0 0" in limits
        assert "Max open files 64 64" in limits
        assert "Max processes 32 32" in limits or not MODAL
        if MODAL:
            kb = int(read("/proc/meminfo").split()[1])
            assert kb <= 256 * 1024, kb
            assert "processor : 1" not in read("/proc/cpuinfo")
            assert "SwapTotal: 0 kB" in read("/proc/meminfo")
            mounts = read("/proc/self/mounts")
            assert "/dev/root / ext4 ro," in mounts, mounts
            assert "tmpfs /work tmpfs rw,nosuid,nodev,noexec" in mounts, mounts
            assert "00000000" not in read("/proc/net/route")
            assert "Permission denied" in read("/proc/1/environ")
            return
        assert read("/sys/fs/cgroup/memory.max") == "268435456"
        assert read("/sys/fs/cgroup/memory.swap.max") == "0"
        assert read("/sys/fs/cgroup/pids.max") == "32"
        assert read("/sys/fs/cgroup/cpu.max") == "100000 100000"
        assert "00000000" not in read("/proc/net/route")  # no default route
        environ = read("/proc/1/environ")
        assert "Permission denied" in environ, environ

    def test_parallel_runs(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(5) as pool:
            assert all(r.ok for r in pool.map(lambda _: run_patch(TONE, 1), range(5)))
