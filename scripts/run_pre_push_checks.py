from __future__ import annotations

import subprocess
import sys
from pathlib import Path


CHECKS: list[tuple[str, list[str]]] = [
    ("Ruff lint", [sys.executable, "-m", "ruff", "check", "src", "tests"]),
    ("Ruff format", [sys.executable, "-m", "ruff", "format", "--check", "src", "tests"]),
    ("Pyright", [sys.executable, "-m", "pyright"]),
    ("Compile check", [sys.executable, "-m", "compileall", "-q", "src", "tests"]),
]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    for label, command in CHECKS:
        print(f"[pre-push] {label}...")
        completed = subprocess.run(command, cwd=root)
        if completed.returncode != 0:
            print(
                f"[pre-push] {label} failed with exit code {completed.returncode}.", file=sys.stderr
            )
            return completed.returncode
    print("[pre-push] All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
