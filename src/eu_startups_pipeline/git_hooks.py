from __future__ import annotations

import subprocess
from pathlib import Path

PRE_PUSH_SCRIPT = """#!/bin/sh
set -eu

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then
  PYTHON_BIN="$ROOT/.venv/Scripts/python.exe"
elif [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
elif command -v py >/dev/null 2>&1; then
  exec py -3 "$ROOT/scripts/run_pre_push_checks.py"
else
  echo "No Python interpreter found for pre-push hook." >&2
  exit 1
fi

exec "$PYTHON_BIN" "$ROOT/scripts/run_pre_push_checks.py"
"""


def install_repo_git_hooks(root: Path) -> Path | None:
    git_dir = root / ".git"
    if not git_dir.exists():
        return None
    hooks_dir = root / ".githooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    pre_push_path = hooks_dir / "pre-push"
    pre_push_path.write_text(PRE_PUSH_SCRIPT, encoding="utf-8", newline="\n")
    pre_push_path.chmod(0o755)
    subprocess.run(
        ["git", "config", "core.hooksPath", ".githooks"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return pre_push_path
