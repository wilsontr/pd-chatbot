"""Run an untrusted ``.pd`` file in a locked-down container or VM.

The limits here and the reasons for them are described in docs/SANDBOX.md.
Everything that comes back from the container is treated as untrusted bytes.
"""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import subprocess
import threading
import time
import uuid
import wave
from dataclasses import dataclass

from sandbox.errors import SandboxError

SAMPLE_RATE = 48_000
CHANNELS = 2
MAX_SECONDS = 30.0
MAX_WALL_SECONDS = 60.0
DEFAULT_WALL_SECONDS = 15.0
MAX_PATCH_BYTES = 1024 * 1024
MAX_CONSOLE_BYTES = 64 * 1024
MEMORY = "256m"

# Time allowed for Docker itself, on top of the run's wall clock.
_STARTUP_GRACE = 20.0
_MAX_HEADER = 4096
_MAX_STDERR = 16 * 1024
_FRAME_BYTES = CHANNELS * 2
_STATUSES = {
    "ok",               # rendered the full duration
    "timeout",          # wall clock ran out
    "memory_limit",     # killed by the kernel for exceeding the memory limit
    "file_size_limit",  # wrote a file larger than allowed
    "crashed",          # Pd died from some other signal
    "load_failed",      # Pd could not open the file as a patch
    "patch_exited",     # the patch quit Pd before the duration was up
}

_slots = threading.BoundedSemaphore(int(os.environ.get("PD_SANDBOX_CONCURRENCY", "2")))



@dataclass(frozen=True)
class SandboxResult:
    status: str
    exit_code: int  # Pd's exit code, or 128 + signal
    signal: int
    console: str
    console_truncated: bool
    wav: bytes  # 16-bit stereo; shorter than requested if the run was stopped
    seconds_rendered: float
    peak: float  # before clipping to 16 bits, so it may exceed 1.0
    nonfinite_samples: int  # NaN/inf samples, written to the WAV as silence

    @property
    def ok(self) -> bool:
        """Ran to the end. The console may still hold Pd errors."""
        return self.status == "ok"


def _docker() -> str:
    path = os.environ.get("PD_SANDBOX_DOCKER") or shutil.which("docker")
    if not path:
        raise SandboxError("docker not found")
    return path


def _command(name: str, seconds: float, wall: float) -> list[str]:
    cmd = [
        _docker(), "run", "--rm", "--interactive", "--pull=never",
        "--name", name, "--label", "pd-sandbox=1",
        "--log-driver=none",
        "--network=none",
        "--read-only",
        "--tmpfs=/work:rw,noexec,nosuid,nodev,size=32m,mode=1777",
        "--ipc=none",
        "--user=65532:65532",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--cpus=1",
        f"--memory={MEMORY}", f"--memory-swap={MEMORY}",
        "--pids-limit=32",
        "--ulimit=fsize=16777216:16777216",
        "--ulimit=nofile=64:64",
        "--ulimit=core=0:0",
    ]
    runtime = os.environ.get("PD_SANDBOX_RUNTIME")
    if runtime:
        cmd.append(f"--runtime={runtime}")
    return [
        *cmd, os.environ.get("PD_SANDBOX_IMAGE", "pd-sandbox:latest"),
        f"{seconds:.6f}", f"{wall:.6f}",
    ]


def _parse(data: bytes, max_frames: int) -> SandboxResult:
    """Parse pdrun's output, checking every field against what is allowed."""
    try:
        end = data.index(b"\n", 0, _MAX_HEADER)
        header = json.loads(data[:end])
        status = header["status"]
        ints = [
            header[k]
            for k in ("exit_code", "signal", "console_bytes", "frames", "nonfinite_samples")
        ]
        peak = float(header["peak"])
        truncated = header["console_truncated"]
        if (
            status not in _STATUSES
            or any(type(v) is not int or v < 0 for v in ints)
            or type(truncated) is not bool
            or not math.isfinite(peak)
        ):
            raise ValueError("bad field")
        exit_code, signal, console_bytes, frames, nonfinite = ints
        body = data[end + 1:]
        if console_bytes > MAX_CONSOLE_BYTES or frames > max_frames:
            raise ValueError("over limit")
        if len(body) != console_bytes + frames * _FRAME_BYTES:
            raise ValueError("wrong length")
    except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as exc:
        raise SandboxError(f"malformed sandbox output: {exc}") from exc

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(body[console_bytes:])
    return SandboxResult(
        status=status,
        exit_code=exit_code,
        signal=signal,
        console=body[:console_bytes].decode("utf-8", "replace"),
        console_truncated=truncated,
        wav=buf.getvalue(),
        seconds_rendered=frames / SAMPLE_RATE,
        peak=peak,
        nonfinite_samples=nonfinite,
    )


def _feed(stream, data: bytes) -> None:
    try:
        stream.write(data)
        stream.close()
    except OSError:
        pass  # the container is gone; its output says why


def _drain(stream, limit: int, out: bytearray, on_overflow) -> None:
    """Read to EOF but keep at most ``limit`` bytes."""
    overflowed = False
    while chunk := stream.read1(65536):
        out += chunk[: limit - len(out) + 1]
        if len(out) > limit and not overflowed:
            overflowed = True
            on_overflow()


def _run(cmd: list[str], name: str, stdin: bytes, limit: int, timeout: float) -> bytes:
    """Run the container and return its stdout; the container never outlives this."""
    deadline = time.monotonic() + timeout
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except OSError as exc:
        raise SandboxError(f"could not start docker: {exc}") from exc

    out, err = bytearray(), bytearray()
    threads = [
        threading.Thread(target=_feed, args=(proc.stdin, stdin), daemon=True),
        threading.Thread(target=_drain, args=(proc.stdout, limit, out, proc.kill), daemon=True),
        threading.Thread(
            target=_drain, args=(proc.stderr, _MAX_STDERR, err, proc.kill), daemon=True
        ),
    ]
    for t in threads:
        t.start()
    clean = False
    try:
        try:
            code = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise SandboxError("sandbox did not finish in time") from None
        for t in threads:
            t.join(timeout=5)
        if len(out) > limit:
            raise SandboxError("sandbox output too large")
        if code != 0:
            detail = err.decode("utf-8", "replace").strip()
            raise SandboxError(f"sandbox failed (exit {code}): {detail}")
        clean = True
        return bytes(out)
    finally:
        if not clean:
            # Killing the docker CLI does not stop the container.
            proc.kill()
            try:
                subprocess.run(
                    [cmd[0], "rm", "--force", name],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass  # the supervisor's own alarm still ends the container
            proc.wait()


def _execute(data: bytes, seconds: float, wall: float, limit: int) -> bytes:
    backend = os.environ.get("PD_SANDBOX_BACKEND", "docker")
    if backend == "modal":
        from sandbox import modal_backend

        return modal_backend.execute(data, seconds, wall, limit)
    if backend != "docker":
        raise SandboxError(f"unknown PD_SANDBOX_BACKEND: {backend}")
    name = f"pd-sandbox-{uuid.uuid4().hex}"
    return _run(
        _command(name, seconds, wall), name, data, limit, wall + _STARTUP_GRACE
    )


def run_patch(
    pd_text: str | bytes,
    seconds: float,
    wall_seconds: float = DEFAULT_WALL_SECONDS,
) -> SandboxResult:
    """Render ``seconds`` of audio from a ``.pd`` file. Blocking.

    Raises ``ValueError`` for bad arguments and ``SandboxError`` if the sandbox
    fails. A misbehaving patch is a normal result with a ``status`` other than "ok".
    """
    data = pd_text.encode() if isinstance(pd_text, str) else pd_text
    if len(data) > MAX_PATCH_BYTES:
        raise ValueError(f"patch is larger than {MAX_PATCH_BYTES} bytes")
    if not 0.001 <= seconds <= MAX_SECONDS:
        raise ValueError(f"seconds must be from 0.001 to {MAX_SECONDS:g}")
    if not 0.1 <= wall_seconds <= MAX_WALL_SECONDS:
        raise ValueError(f"wall_seconds must be from 0.1 to {MAX_WALL_SECONDS:g}")

    max_frames = math.ceil(seconds * SAMPLE_RATE) + 1
    limit = _MAX_HEADER + MAX_CONSOLE_BYTES + max_frames * _FRAME_BYTES
    if not _slots.acquire(timeout=MAX_WALL_SECONDS):
        raise SandboxError("sandbox is busy")
    try:
        raw = _execute(data, seconds, wall_seconds, limit)
    finally:
        _slots.release()
    return _parse(raw, max_frames)
