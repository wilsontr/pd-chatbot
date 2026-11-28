"""The one marker for tests that run patches in the pd-sandbox image (TASK-5.35).

A test that needs the image is decorated with ``needs_sandbox``. That marks it
``sandbox``, so ``pytest -m sandbox`` selects every such test (the Sandbox CI job
runs exactly that), and skips it when the image is missing — unless
``PD_SANDBOX_REQUIRED`` is set, in which case it runs and fails, so a broken image
build cannot pass CI by skipping everything.

Build the image with ``docker build -t pd-sandbox sandbox/``, or set
``PD_SANDBOX_BACKEND=modal`` to run on Modal instead.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess

import pytest

IMAGE = os.environ.get("PD_SANDBOX_IMAGE", "pd-sandbox:latest")
MODAL = os.environ.get("PD_SANDBOX_BACKEND") == "modal"
REQUIRED = bool(os.environ.get("PD_SANDBOX_REQUIRED"))


@functools.lru_cache(maxsize=1)
def image_ready() -> bool:
    if MODAL:
        return True
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        return subprocess.run(
            [docker, "image", "inspect", IMAGE], capture_output=True, timeout=30
        ).returncode == 0
    except subprocess.TimeoutExpired:
        return False


_skip = pytest.mark.skipif(
    not REQUIRED and not image_ready(), reason="pd-sandbox image not built"
)


def needs_sandbox(obj):
    """Mark a test function or class as needing the sandbox image."""
    return pytest.mark.sandbox(_skip(obj))
