from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_settings
from .git_hooks import install_repo_git_hooks
from .logging_utils import configure_logging
from .pipeline import PipelineRunner, format_status_report
from .review import apply_review_resolutions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eu-startups-pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize local folders, schema, and review templates.")

    run_parser = subparsers.add_parser(
        "run", help="Start a fresh crawl from the configured search URL."
    )
    run_parser.add_argument(
        "--reset-state", action="store_true", help="Clear persisted pipeline state first."
    )
    run_parser.add_argument("--max-tasks", type=int, default=None, help="Process at most N tasks.")

    resume_parser = subparsers.add_parser("resume", help="Resume pending work from SQLite state.")
    resume_parser.add_argument(
        "--max-tasks", type=int, default=None, help="Process at most N tasks."
    )

    subparsers.add_parser("status", help="Show progress counts.")
    subparsers.add_parser(
        "export", help="Regenerate clean outputs and review files from persisted state."
    )
    subparsers.add_parser("apply-review", help="Apply review resolutions and regenerate exports.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings(Path.cwd())
    configure_logging(settings.paths.log_dir)
    runner = PipelineRunner(settings)
    try:
        if args.command == "init":
            runner.init_workspace()
            hook_path = install_repo_git_hooks(settings.paths.root)
            print(f"Initialized workspace at {settings.paths.root}")
            if hook_path is not None:
                print(f"Installed repo pre-push hook at {hook_path}")
            return 0
        if args.command == "run":
            runner.start_new_run(reset_state=args.reset_state)
            processed = runner.resume(max_tasks=args.max_tasks)
            print(f"Processed {processed} tasks.")
            print(format_status_report(runner.status_counts(), settings.paths.output_dir))
            return 0
        if args.command == "resume":
            processed = runner.resume(max_tasks=args.max_tasks)
            print(f"Processed {processed} tasks.")
            print(format_status_report(runner.status_counts(), settings.paths.output_dir))
            return 0
        if args.command == "status":
            print(format_status_report(runner.status_counts(), settings.paths.output_dir))
            return 0
        if args.command == "export":
            count = runner.regenerate_exports()
            print(f"Regenerated exports with {count} rows in {settings.paths.output_dir}")
            return 0
        if args.command == "apply-review":
            applied = apply_review_resolutions(
                runner.db, settings.paths.output_dir / "review_resolutions.csv"
            )
            count = runner.regenerate_exports()
            print(f"Applied {applied} review resolutions.")
            print(f"Regenerated exports with {count} rows in {settings.paths.output_dir}")
            return 0
        parser.error(f"Unknown command: {args.command}")
        return 2
    finally:
        runner.close()
