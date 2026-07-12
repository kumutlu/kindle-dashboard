#!/usr/bin/env python3
"""Render legacy and default-kindle images for the production timer."""

from __future__ import annotations

from datetime import datetime
import subprocess
import sys


RENDERS = (
    ("legacy", [sys.executable, "weather_image.py"]),
    (
        "default-kindle",
        [
            sys.executable,
            "weather_image.py",
            "--device",
            "default-kindle",
        ],
    ),
    (
        "kindle-131",
        [
            sys.executable,
            "weather_image.py",
            "--device",
            "kindle-131",
        ],
    ),
)


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def main() -> int:
    failed = False
    for target, command in RENDERS:
        print(f"{timestamp()} render_start target={target}", flush=True)
        result = subprocess.run(command, check=False)
        if result.returncode == 0:
            print(
                f"{timestamp()} render_complete target={target}",
                flush=True,
            )
        else:
            failed = True
            print(
                f"{timestamp()} render_failed target={target} "
                f"rc={result.returncode}",
                file=sys.stderr,
                flush=True,
            )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
