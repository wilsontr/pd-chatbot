"""python -m sandbox patch.pd --seconds 3 --out out.wav"""

import argparse
import sys
from pathlib import Path

from sandbox.runner import DEFAULT_WALL_SECONDS, SandboxError, run_patch


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m sandbox", description=__doc__)
    parser.add_argument("patch", type=Path)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--wall", type=float, default=DEFAULT_WALL_SECONDS)
    parser.add_argument("--out", type=Path, help="where to write the WAV")
    args = parser.parse_args()
    try:
        result = run_patch(args.patch.read_bytes(), args.seconds, args.wall)
    except (OSError, ValueError, SandboxError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(result.console, end="")
    print(
        f"status={result.status} exit_code={result.exit_code} "
        f"rendered={result.seconds_rendered:.2f}s peak={result.peak:.3f}",
        file=sys.stderr,
    )
    if args.out:
        args.out.write_bytes(result.wav)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
