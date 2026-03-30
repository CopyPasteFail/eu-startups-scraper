from __future__ import annotations

from .db import Database
from .models import CodexReviewTarget


def next_codex_action(db: Database, counts: dict[str, int]) -> str:
    target = db.next_codex_review_target()
    if target is not None:
        return (
            f"Next Codex action: investigate `{target.company_name}` "
            f"via `{target.review_type}` and write a resolution."
        )
    if counts["remaining_queued_work"] > 0:
        return "Next Codex action: resume the deterministic pipeline queue."
    return "Next Codex action: no open review targets and no queued work remain."


def format_codex_review_target(target: CodexReviewTarget | None) -> str:
    if target is None:
        return "No open Codex review target."
    lines = [
        f"review_id: {target.review_id}",
        f"review_type: {target.review_type}",
        f"company: {target.company_name}",
        f"company_url: {target.company_url}",
        f"website_url: {target.website_url or '-'}",
        f"company_linkedin: {target.company_linkedin or '-'}",
        f"total_funding: {target.total_funding or '-'}",
        f"funding_stage: {target.funding_stage or '-'}",
        f"subject: {target.subject or '-'}",
        f"proposed_value: {target.proposed_value or '-'}",
    ]
    if target.candidate_values:
        lines.append(f"candidate_values: {' | '.join(target.candidate_values)}")
    if target.context:
        lines.append("context:")
        for key in sorted(target.context):
            lines.append(f"  {key}: {target.context[key]}")
    lines.append("resolution_format:")
    if target.review_type == "company_people_research":
        lines.append("  add person: resolution_action=add_person")
        lines.append("  resolution_value=Name | Role | LinkedIn URL")
        lines.append("  finish company: resolution_action=done")
        lines.append("  no public match: resolution_action=no_match")
    elif target.review_type == "linkedin_company":
        lines.append("  resolution_action=set")
        lines.append("  resolution_value=https://www.linkedin.com/company/<slug>/people")
    elif target.review_type == "person_include":
        lines.append("  resolution_action=include or exclude")
        lines.append("  resolution_value can be left blank")
    return "\n".join(lines)
