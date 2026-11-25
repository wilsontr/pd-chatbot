"""Run pdrun in a Modal sandbox. See docs/SANDBOX.md for what Modal enforces.

Modal starts pdrun as root and ignores ``USER``, so pdrun applies the limits
Docker would have applied. That needs the VM runtime: gVisor refuses the mounts,
and pdrun then ends the run.
"""

from __future__ import annotations

import functools
import math
import os
from pathlib import Path

from sandbox.errors import SandboxError

APP_NAME = os.environ.get("PD_SANDBOX_MODAL_APP", "pd-sandbox")
_STARTUP_GRACE = 30


@functools.cache
def _app_and_image():
    import modal

    here = Path(__file__).parent
    app = modal.App.lookup(APP_NAME, create_if_missing=True)
    return app, modal.Image.from_dockerfile(here / "Dockerfile", context_dir=here)


def execute(data: bytes, seconds: float, wall: float, limit: int) -> bytes:
    try:
        import modal
    except ImportError as exc:
        raise SandboxError("the modal package is not installed") from exc

    lifetime = math.ceil(wall) + _STARTUP_GRACE
    sandbox = None
    try:
        app, image = _app_and_image()
        sandbox = modal.Sandbox.create(
            "hold", str(lifetime),  # arguments to the image's ENTRYPOINT, pdrun
            app=app,
            image=image,
            runtime="vm",
            block_network=True,
            cpu=(0.5, 0.5),  # physical cores: one vCPU
            memory=(256, 256),
            timeout=lifetime,
        )
        # exec, because a main process's output goes through Modal's logs, which
        # are kept, rate-limited and text only.
        proc = sandbox.exec(
            "/pdrun", f"{seconds:.6f}", f"{wall:.6f}", text=False, timeout=lifetime
        )
        try:
            proc.stdin.write(data)
            proc.stdin.write_eof()
            proc.stdin.drain()
        except modal.exception.Error:
            pass  # it has already exited; its stderr says why

        out = bytearray()
        # EOF is guaranteed by the sandbox timeout.
        for chunk in proc.stdout:
            out += chunk
            if len(out) > limit:
                raise SandboxError("sandbox output too large")
        code = proc.wait()
        if code != 0:
            detail = proc.stderr.read()[:2000].decode("utf-8", "replace").strip()
            raise SandboxError(f"sandbox failed (exit {code}): {detail}")
        return bytes(out)
    except (modal.exception.Error, OSError) as exc:
        raise SandboxError(f"modal sandbox failed: {exc}") from exc
    finally:
        if sandbox is not None:
            try:
                sandbox.terminate()
            except Exception:
                pass  # its own timeout still ends it
