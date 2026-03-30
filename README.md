# EU-Startups scraper

Local, slow, resumable scraping and lead-enrichment pipeline for EU-Startups search results.

Codex app is the default ambiguity solver and review orchestrator for this repo. The runtime pipeline is deterministic by default, does not require `OPENAI_API_KEY`, and writes ambiguous cases to review files for manual resolution through Codex app.

## Quick start

1. Create a virtualenv and install the package:
   `python -m venv .venv`
   `.venv\Scripts\activate`
   `python -m pip install -e .[dev]`
2. Install the Playwright browser once:
   `python -m playwright install chromium`
3. Copy `.env.example` to `.env`.
4. Edit `.env` and `config/pipeline_policy.json`.
5. Bootstrap local folders:
   `python -m eu_startups_pipeline init`
   This also installs the repo-managed Git `pre-push` hook by setting `core.hooksPath` to `.githooks`.
6. Start a fresh crawl:
   `python -m eu_startups_pipeline run --reset-state`

## Default ambiguity workflow

This is the primary supported workflow.

1. Run the pipeline from scratch:
   `python -m eu_startups_pipeline run --reset-state`
2. Resume later if needed:
   `python -m eu_startups_pipeline resume`
3. Check progress:
   `python -m eu_startups_pipeline status`
4. Export current clean outputs:
   `python -m eu_startups_pipeline export`
5. Inspect ambiguous cases in `runtime/exports/review_queue.csv` or `runtime/exports/review_queue.xlsx`.
6. Use Codex app to inspect those review files and write or update `runtime/exports/review_resolutions.csv`.
7. Apply the resolutions:
   `python -m eu_startups_pipeline apply-review`
8. Regenerate clean outputs again if needed:
   `python -m eu_startups_pipeline export`

## Commands

- `python -m eu_startups_pipeline init`
- `python -m eu_startups_pipeline run`
- `python -m eu_startups_pipeline resume`
- `python -m eu_startups_pipeline status`
- `python -m eu_startups_pipeline export`
- `python -m eu_startups_pipeline apply-review`

## Pre-push checks

`python -m eu_startups_pipeline init` installs a repo-local Git `pre-push` hook. The hook runs lightweight checks from `scripts/run_pre_push_checks.py`:

- `ruff check src tests`
- `ruff format --check src tests`
- `pyright`
- `python -m compileall -q src tests`

These are intentionally lightweight and popular for Python repos:

- `ruff check` catches lint issues, unused imports, simple bug patterns, and import ordering.
- `ruff format --check` keeps formatting consistent without a heavier formatter stack.
- `pyright` adds stricter static type checking with low overlap with Ruff.
- `compileall` gives a fast syntax/import-time sanity check.

The tool versions are pinned in `pyproject.toml`. Current pinned versions are:

- `ruff==0.15.8`
- `pyright==1.1.408`
- `bandit==1.9.4`
- `pip-audit==2.10.0`
- `deptry==0.25.1`

## GitHub Actions checks

The repo also includes a GitHub Actions workflow at `.github/workflows/checks.yml` for the heavier checks:

- `bandit`
- `pip-audit`
- `deptry`

These are kept out of the default `pre-push` hook because they are slower and more likely to fail due to ecosystem or security-feed changes rather than a local code edit.

## Pagination and stopping

- Search pagination follows the actual next-page link found in the EU-Startups result HTML. The crawler does not infer page counts or synthesize `/page/N/` URLs.
- Next-page discovery accepts several real-world variants: `a.next-page`, `a[rel='next']`, and text-based next links such as `Next` or `Next ->`.
- The crawl stops only when a result page has no discoverable next-page link, or when a repeated page signature is confirmed after one forced refresh recheck.
- Transient fetch failures and active challenge pages are retried through the persisted task queue instead of being treated as the end of pagination.

## Practical crawling note

- The pagination logic is designed for full discovery from page 1 onward for large searches such as the Berlin query.
- EU-Startups can still present a Cloudflare challenge to the local browser session. When that happens, the pipeline now postpones and retries the affected task instead of silently parsing the challenge page as an empty final page.
- For the first real full crawl, use `python -m eu_startups_pipeline run --reset-state`, then use `python -m eu_startups_pipeline resume` until `python -m eu_startups_pipeline status` shows no remaining queued work.

## Runtime layout

- Config: `.env`, `config/pipeline_policy.json`
- State DB: `runtime/state/pipeline.sqlite3`
- Raw cached pages: `runtime/raw/`
- Logs: `runtime/logs/`
- Outputs: `runtime/exports/`

## Runtime LLM mode

`OPENAI_API_KEY` is not required for the normal pipeline or review workflow.

- Default mode: deterministic runtime pipeline plus review files handled through Codex app.
- Optional future mode: `ENABLE_RUNTIME_LLM=1` with `OPENAI_*` settings reserved for a later API-based ambiguity path.
- Current primary path: leave `ENABLE_RUNTIME_LLM=0`.

## Review flow

1. Run `run` or `resume`.
2. Review `runtime/exports/review_queue.csv` or `runtime/exports/review_queue.xlsx` in Codex app.
3. Have Codex app write or update `runtime/exports/review_resolutions.csv`.
4. Apply decisions:
   `python -m eu_startups_pipeline apply-review`
5. Regenerate clean outputs if needed:
   `python -m eu_startups_pipeline export`
