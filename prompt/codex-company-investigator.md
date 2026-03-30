# Codex Company Investigator

Use this prompt when `python -m eu_startups_pipeline chat-review-next` surfaces a company that still needs human-guided investigation after deterministic enrichment.

## Goal

Investigate the surfaced company using public evidence and write structured resolutions into `runtime/exports/review_resolutions.csv`.

## Priorities

1. Find high-confidence relevant people:
   - CEO
   - CTO
   - Founder / Co-Founder
   - Head of Engineering
   - VP R&D
2. Confirm or improve the company LinkedIn page only when the existing link looks weak or wrong.
3. Avoid guessing.

## Allowed evidence

- EU-Startups company page
- company website
- public company/team/about/contact/imprint pages
- public search results
- publicly reachable LinkedIn URLs

## Resolution rules

For `company_people_research`:

- add one row per person with:
  - `resolution_action=add_person`
  - `resolution_value=Name | Role | LinkedIn URL`
- after adding all supported people, add one final row with:
  - `resolution_action=done`
- if no strong public match exists, add one final row with:
  - `resolution_action=no_match`

For `linkedin_company`:

- use:
  - `resolution_action=set`
  - `resolution_value=https://www.linkedin.com/company/<slug>/people`

For `person_include`:

- use:
  - `resolution_action=include`
  - or `resolution_action=exclude`

## Quality bar

- Prefer fewer strong matches over more weak ones.
- If role equivalence is uncertain, explain it in `resolution_notes`.
- If LinkedIn access is visibility-limited, use the strongest public evidence available and note the limitation.
