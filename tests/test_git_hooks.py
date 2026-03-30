import shutil
import subprocess
from pathlib import Path

from eu_startups_pipeline.git_hooks import install_repo_git_hooks


def test_install_repo_git_hooks_configures_core_hooks_path(tmp_path: Path):
    if shutil.which("git") is None:
        return

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)

    hook_path = install_repo_git_hooks(tmp_path)

    assert hook_path == tmp_path / ".githooks" / "pre-push"
    assert hook_path.exists()
    assert "run_pre_push_checks.py" in hook_path.read_text(encoding="utf-8")

    hooks_path = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert hooks_path == ".githooks"
