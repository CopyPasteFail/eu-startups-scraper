# AGENTS.md

## Purpose

This repo is a local, cache-heavy EU-Startups-specific scraper and enrichment pipeline. It starts from a configurable EU-Startups search URL, crawls search results in order, parses company pages, filters by configured funding buckets, enriches companies with website and LinkedIn discovery, tries to find relevant people, and exports clean CSV/XLSX plus a human-editable review queue.

Codex app is the default ambiguity solver and external orchestrator. The runtime pipeline itself should stay deterministic by default and should not require `OPENAI_API_KEY` for normal operation.

## Entrypoint

Use `python -m eu_startups_pipeline <command>`.

Primary commands:

- `init`: create runtime folders, initialize SQLite schema, and materialize review templates.
- `run`: start a fresh crawl from the configured `EU_STARTUPS_SEARCH_URL`. This refuses to overwrite non-empty state unless `--reset-state` is passed.
- `resume`: continue pending work from persisted state without refetching cached pages unless refresh flags are enabled.
- `status`: print crawl/enrichment/export counts.
- `export`: regenerate `results_clean.csv`, `results_clean.xlsx`, `review_queue.csv`, `review_queue.xlsx`, and `review_resolutions.csv` from persisted state.
- `apply-review`: read `review_resolutions.csv`, apply Codex-assisted human decisions, and regenerate exports.
- `chat-start`: start a chat-first run and then print the next Codex action.
- `chat-resume`: resume a chat-first run and then print the next Codex action.
- `chat-status`: show progress plus the next Codex action.
- `chat-review-next`: print the next open company/ambiguity target for Codex investigation.
- `chat-apply`: apply review resolutions, regenerate exports, and print the next Codex action.
- `chat-export`: regenerate exports and print the next Codex action.

`init` is also responsible for repo setup tasks. It should install the repo-managed Git `pre-push` hook by setting `core.hooksPath` to `.githooks`.
The default pre-push hook should run `ruff`, `pyright`, and a fast compile check. Heavier checks belong in GitHub Actions.

## Primary ambiguity workflow

The default workflow is:

1. `python -m eu_startups_pipeline run --reset-state`
2. `python -m eu_startups_pipeline resume`
3. `python -m eu_startups_pipeline status`
4. `python -m eu_startups_pipeline export`
5. inspect `runtime/exports/review_queue.csv` or `runtime/exports/review_queue.xlsx`
6. use Codex app to review ambiguous cases and write or update `runtime/exports/review_resolutions.csv`
7. `python -m eu_startups_pipeline apply-review`
8. `python -m eu_startups_pipeline export`

`review_resolutions.csv` is the merge point back into the deterministic pipeline.

For a more hands-off Codex-first session, prefer:

1. `python -m eu_startups_pipeline chat-start --reset-state`
2. `python -m eu_startups_pipeline chat-resume`
3. `python -m eu_startups_pipeline chat-status`
4. `python -m eu_startups_pipeline chat-review-next`
5. have Codex investigate the surfaced company or ambiguity target
6. have Codex write `runtime/exports/review_resolutions.csv`
7. `python -m eu_startups_pipeline chat-apply`
8. `python -m eu_startups_pipeline chat-export`

Codex should treat `chat-review-next` as the repo-controlled trigger for the next investigation target instead of waiting for the user to name a company manually.

## Resume semantics

In this repo, "resume" means:

- reset any previously running tasks back to `pending`
- continue the persisted task queue from SQLite
- reuse cached raw pages from `runtime/raw/`
- avoid refetching URLs unless the corresponding refresh flag is enabled
- preserve prior review items, people, enrichments, and parsed company records

## Finished semantics

For this repo, a run is "finished" when:

- no crawl/enrichment tasks remain pending
- `status` reports zero remaining queued work
- review artifacts were regenerated
- clean exports were regenerated from persisted state

The overall project is "finished" only when the queue is empty and the human review queue is either empty or intentionally deferred.

## Codex investigation trigger

When a company passes the funding filter:

- deterministic enrichment must run first
- if deterministic enrichment still cannot produce the needed people, the repo should create a `company_people_research` review item automatically
- Codex can then pick that item up with `chat-review-next` and investigate without waiting for a new user prompt naming the company

This keeps the runtime deterministic while allowing Codex app to operate as the external orchestrator for the unresolved cases.

For `company_people_research` items, `review_resolutions.csv` should use:

- `resolution_action=add_person`
- `resolution_value=Name | Role | LinkedIn URL`

Codex may write multiple `add_person` rows for the same `review_id`, then finish with:

- `resolution_action=done`

or:

- `resolution_action=no_match`

## Runtime LLM mode

- The main path must run end-to-end without any OpenAI key.
- `ENABLE_RUNTIME_LLM=0` is the default and should remain the default.
- `OPENAI_API_KEY`, `OPENAI_MODEL`, and `OPENAI_BASE_URL` are optional future-mode settings only.
- Do not make the main scraping/enrichment path depend on direct API calls.
- Prefer writing ambiguous cases to review artifacts instead of guessing.

## Source-site assumptions

- EU-Startups search results use `wpbdp` listing markup.
- EU-Startups company pages expose `Total Funding` as a structured field.
- Funding filtering must be based on bucket text, not funding stage.
- Funding stage is optional enrichment only.

## Pagination expectations

- Search-page discovery must follow actual next-page links from the HTML response. Do not infer total page counts and do not generate synthetic page-number URLs.
- Treat next-link detection as tolerant parsing: accept `a.next-page`, `a[rel='next']`, and text-based next links when the markup varies.
- Stop only when there is truly no next-page link or when a repeated result-page signature is confirmed after a forced refresh recheck.
- Never treat a transient fetch error or an active challenge page as proof that pagination is finished.
- If EU-Startups presents Cloudflare, the correct behavior is to postpone and retry via the persisted queue, not to record an empty search page and stop.

## Guardrails for future Codex sessions

- Keep the project slow and sequential by default.
- Favor persistence and resumability over speed.
- Never make LinkedIn the backbone of the pipeline.
- Reuse the cached raw HTML and SQLite state whenever possible.
- Treat ambiguous matching as review-queue material, not as a place to guess.
- Treat Codex app plus `review_resolutions.csv` as the primary ambiguity-resolution loop.
- Do not introduce a runtime OpenAI dependency into the default path.
- Keep the default pre-push checks lightweight. Prefer `ruff`, `pyright`, and other fast static checks over heavy gates.
- Put `bandit`, `pip-audit`, and `deptry` in GitHub Actions rather than the default pre-push hook.
- Keep exported column order stable:
  1. Company
  2. EU-link
  3. Company LinkedIn
  4. Person
  5. Role
  6. Total funding
  7. Funding stage
