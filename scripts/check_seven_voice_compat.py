#!/usr/bin/env python3
"""Check the Larynx listener patch against a Seven Voice checkout.

The source checkout is copied to a temporary directory before patching, so this
command never changes the caller's Seven Voice tree. It verifies that the patch
applies, preserves Seven Voice V2's streaming path, compiles, is idempotent, and
passes the upstream helper tests. Use --streaming-tests when the selected Python
environment has Seven Voice's dependencies and ffmpeg installed.

Example:
    python3 scripts/check_seven_voice_compat.py ../seven-voice \
        --python ../seven-voice/.venv/bin/python --streaming-tests
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = ROOT / "patch_seven_voice_listener_recovery.py"


def run(command: list[str], *, cwd: Path, label: str) -> None:
    print(f"\n==> {label}", flush=True)
    print("$", shlex.join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def resolve_executable(value: str) -> str | None:
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        # Do not use Path.resolve(): a virtualenv's python is commonly a symlink
        # to the system interpreter, and following it discards the venv context.
        absolute = Path(os.path.abspath(candidate))
        return str(absolute) if absolute.is_file() else None
    found = shutil.which(value)
    return found


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seven_voice", type=Path, help="path to a Seven Voice checkout")
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter with Seven Voice dependencies (default: current Python)",
    )
    parser.add_argument(
        "--expected-commit",
        help="refuse to run unless the source checkout is at this exact commit",
    )
    parser.add_argument(
        "--streaming-tests",
        action="store_true",
        help="also run upstream test_streaming.py (requires dependencies and ffmpeg)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.seven_voice.expanduser().resolve()
    python = resolve_executable(args.python)
    if python is None:
        print(f"error: Python interpreter {args.python!r} not found", file=sys.stderr)
        return 2
    source_file = source / "seven_voice.py"
    if not source_file.is_file():
        print(f"error: {source_file} not found", file=sys.stderr)
        return 2

    if args.expected_commit:
        actual = git_head(source)
        if actual is None:
            print("error: --expected-commit requires a Git checkout", file=sys.stderr)
            return 2
        if actual != args.expected_commit:
            print(
                f"error: expected Seven Voice {args.expected_commit}, found {actual}",
                file=sys.stderr,
            )
            return 2
        print(f"Seven Voice commit: {actual}")

    with tempfile.TemporaryDirectory(prefix="larynx-seven-voice-") as temp:
        work = Path(temp) / "seven-voice"
        shutil.copytree(
            source,
            work,
            ignore=shutil.ignore_patterns(
                ".git",
                ".venv",
                ".env",
                "__pycache__",
                "*.bak-*",
                "*.log",
            ),
        )
        patched = work / "seven_voice.py"

        run(
            [python, str(PATCH_SCRIPT), str(patched)],
            cwd=work,
            label="apply listener recovery patch",
        )

        text = patched.read_text(encoding="utf-8")
        required_markers = (
            "voice_channel_id: int | None = None",
            "def arm_voice_listener",
            "def voice_watchdog",
            "def play_stream",
            "stream_first_audio_timeout",
        )
        missing = [marker for marker in required_markers if marker not in text]
        if missing:
            print(f"error: patched source is missing markers: {missing}", file=sys.stderr)
            return 1

        first_hash = sha256(patched)
        run(
            [python, str(PATCH_SCRIPT), str(patched)],
            cwd=work,
            label="verify patch idempotence",
        )
        if sha256(patched) != first_hash:
            print("error: applying the patch twice changed seven_voice.py", file=sys.stderr)
            return 1

        run(
            [python, "-m", "py_compile", "seven_voice.py"],
            cwd=work,
            label="compile patched Seven Voice",
        )
        run(
            [python, "-m", "unittest", "test_helpers.py"],
            cwd=work,
            label="run upstream helper tests",
        )

        if args.streaming_tests:
            streaming_test = work / "test_streaming.py"
            if not streaming_test.is_file():
                print(f"error: {streaming_test} not found", file=sys.stderr)
                return 1
            run(
                [python, "test_streaming.py"],
                cwd=work,
                label="run upstream V2 streaming tests",
            )

    print("\nSeven Voice compatibility check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
