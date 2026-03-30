from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import Settings, ensure_runtime_dirs
from .db import Database
from .enrichment import discover_company_linkedin, discover_people
from .exporters import export_results
from .fetching import ChallengeBlockedError, CooldownError, Fetcher
from .funding import funding_bucket_allowed
from .parsers import looks_like_valid_company_page, parse_company_page, parse_search_results
from .review import export_review_files, make_review_item
from .utils import load_json, normalize_domain

LOGGER = logging.getLogger(__name__)


class PipelineRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        ensure_runtime_dirs(settings)
        self.db = Database(settings.paths.db_path)
        self.db.initialize()

    def close(self) -> None:
        self.db.close()

    def init_workspace(self) -> None:
        ensure_runtime_dirs(self.settings)
        self.db.initialize()
        export_review_files(self.db, self.settings.paths.output_dir)

    def start_new_run(self, *, reset_state: bool = False) -> None:
        if reset_state:
            self.db.reset_state()
        elif (
            self.db.status_counts()["remaining_queued_work"]
            or self.db.status_counts()["company_pages_parsed"]
        ):
            raise RuntimeError("Existing state detected. Use `resume` or pass `--reset-state`.")
        self.db.enqueue_task(
            "crawl_search_page",
            url=self.settings.search_url,
            domain=normalize_domain(self.settings.search_url),
            unique_key=f"crawl_search_page:{self.settings.search_url}",
        )

    def resume(self, *, max_tasks: int | None = None) -> int:
        self.db.reset_running_tasks()
        processed = self._run_loop(max_tasks=max_tasks)
        self.regenerate_exports()
        return processed

    def regenerate_exports(self) -> int:
        self.settings.paths.output_dir.mkdir(parents=True, exist_ok=True)
        export_review_files(self.db, self.settings.paths.output_dir)
        return export_results(self.db, self.settings.paths.output_dir)

    def status_counts(self) -> dict[str, int]:
        return self.db.status_counts()

    def _run_loop(self, *, max_tasks: int | None = None) -> int:
        processed = 0
        fetcher = Fetcher(self.settings, self.db)
        try:
            while True:
                if max_tasks is not None and processed >= max_tasks:
                    break
                task = self.db.next_task()
                if task is None:
                    break
                try:
                    self._process_task(task, fetcher)
                    self.db.complete_task(task["id"])
                    processed += 1
                except CooldownError as exc:
                    not_before = (
                        (datetime.now(tz=UTC) + timedelta(seconds=exc.seconds))
                        .replace(microsecond=0)
                        .isoformat()
                    )
                    self.db.set_domain_cooldown(exc.domain, not_before)
                    self.db.postpone_task(task["id"], not_before, str(exc))
                except ChallengeBlockedError as exc:
                    not_before = (
                        (datetime.now(tz=UTC) + timedelta(seconds=exc.seconds))
                        .replace(microsecond=0)
                        .isoformat()
                    )
                    self.db.set_domain_cooldown(exc.domain, not_before)
                    self.db.postpone_task(task["id"], not_before, str(exc))
                except Exception as exc:
                    LOGGER.exception("task %s failed", task["task_type"])
                    if int(task["attempts"]) <= self.settings.max_retries:
                        delay_seconds = min(300, 30 * int(task["attempts"]))
                        not_before = (
                            (datetime.now(tz=UTC) + timedelta(seconds=delay_seconds))
                            .replace(microsecond=0)
                            .isoformat()
                        )
                        self.db.postpone_task(
                            task["id"], not_before, f"retrying after error: {exc}"
                        )
                    else:
                        self.db.fail_task(task["id"], str(exc))
        finally:
            fetcher.close()
        return processed

    def _process_task(self, task: Any, fetcher: Fetcher) -> None:
        task_type = task["task_type"]
        payload = load_json(task["payload_json"], {})
        if task_type == "crawl_search_page":
            self._process_search_page(task["url"], fetcher, payload)
            return
        if task_type == "crawl_company_page":
            self._process_company_page(task["company_url"], fetcher)
            return
        if task_type == "enrich_company":
            self._process_company_enrichment(task["company_url"], fetcher)
            return
        raise RuntimeError(f"Unsupported task type: {task_type} with payload {payload}")

    def _process_search_page(self, url: str, fetcher: Fetcher, payload: dict[str, Any]) -> None:
        fetched = fetcher.fetch(
            url,
            kind="eu_search_page",
            refresh=self.settings.refresh_search_pages or payload.get("force_refresh", False),
            prefer_browser=True,
        )
        parsed = parse_search_results(fetched.content, fetched.final_url)
        repeated = self.db.seen_signature(parsed.signature)
        if repeated and not payload.get("duplicate_check_performed"):
            fetched = fetcher.fetch(url, kind="eu_search_page", refresh=True, prefer_browser=True)
            parsed = parse_search_results(fetched.content, fetched.final_url)
            repeated = self.db.seen_signature(parsed.signature)
        self.db.record_search_page(parsed, fetched.body_path, repeated)
        for listing in parsed.listings:
            self.db.upsert_company_stub(listing, parsed.page_url)
            self.db.enqueue_task(
                "crawl_company_page",
                company_url=listing.company_url,
                url=listing.company_url,
                domain=normalize_domain(listing.company_url),
                unique_key=f"crawl_company_page:{listing.company_url}",
            )
        if parsed.next_url and not repeated:
            self.db.enqueue_task(
                "crawl_search_page",
                url=parsed.next_url,
                domain=normalize_domain(parsed.next_url),
                unique_key=f"crawl_search_page:{parsed.next_url}",
            )

    def _process_company_page(self, company_url: str, fetcher: Fetcher) -> None:
        fetched = fetcher.fetch(
            company_url,
            kind="eu_company_page",
            refresh=self.settings.refresh_company_pages,
            prefer_browser=True,
        )
        if not looks_like_valid_company_page(fetched.content):
            raise ChallengeBlockedError(
                normalize_domain(fetched.final_url or company_url),
                120,
                f"Company page content not ready for {company_url}",
            )
        record = parse_company_page(
            fetched.content, fetched.final_url, self.settings.funding_policy
        )
        existing = self.db.get_company(company_url)
        record.search_page_url = existing["search_page_url"] if existing else ""
        record.html_path = fetched.body_path
        record.passes_funding_filter = (
            self.settings.disable_funding_filter
            or funding_bucket_allowed(record.funding_bucket_key, self.settings.funding_policy)
        )
        self.db.upsert_company(record)
        if record.passes_funding_filter:
            self.db.enqueue_task(
                "enrich_company",
                company_url=record.company_url,
                url=record.website_url or record.company_url,
                domain=normalize_domain(record.website_url or record.company_url),
                unique_key=f"enrich_company:{record.company_url}",
            )

    def _process_company_enrichment(self, company_url: str, fetcher: Fetcher) -> None:
        company = self.db.get_company(company_url)
        if not company:
            raise RuntimeError(f"Missing company for enrichment: {company_url}")
        linkedin_url, linkedin_source, confidence, pages_scanned = discover_company_linkedin(
            company_name=company["company_name"],
            company_url=company["company_url"],
            website_url=company["website_url"] or "",
            eu_linkedin_url=company["eu_linkedin_url"] or "",
            settings=self.settings,
            fetcher=fetcher,
            db=self.db,
        )
        people = discover_people(
            company_name=company["company_name"],
            company_url=company["company_url"],
            website_url=company["website_url"] or "",
            settings=self.settings,
            fetcher=fetcher,
            db=self.db,
        )
        self.db.upsert_enrichment(
            company_url=company_url,
            linkedin_company_url=linkedin_url,
            linkedin_source=linkedin_source,
            linkedin_confidence=confidence,
            linkedin_needs_review=confidence < 0.9 and bool(linkedin_url),
            website_checked=True,
            pages_scanned=pages_scanned,
            notes="",
        )
        self.db.replace_people(company_url, people)
        included_people = [person for person in people if person.include_in_output]
        if included_people:
            self.db.close_review_items("company_people_research", company_url)
        else:
            self.db.upsert_review_item(
                make_review_item(
                    review_type="company_people_research",
                    company_url=company_url,
                    company_name=company["company_name"],
                    subject=company["company_name"],
                    proposed_value="",
                    candidate_values=[],
                    context={
                        "website_url": company["website_url"] or "",
                        "linkedin_company_url": linkedin_url,
                        "total_funding": company["total_funding_display"]
                        or company["total_funding_raw"]
                        or "",
                        "funding_stage": company["funding_stage"] or "",
                        "next_step": "Investigate likely founders or engineering leaders and add them via review_resolutions.csv",
                    },
                )
            )


def format_status_report(counts: dict[str, int], output_dir: Path) -> str:
    return "\n".join(
        [
            f"search pages fetched: {counts['search_pages_fetched']}",
            f"company pages parsed: {counts['company_pages_parsed']}",
            f"companies passing funding filter: {counts['companies_passing_funding_filter']}",
            f"companies enriched: {counts['companies_enriched']}",
            f"people found: {counts['people_found']}",
            f"fallback company rows generated: {counts['fallback_company_rows_generated']}",
            f"ambiguous items in review queue: {counts['ambiguous_items_in_review_queue']}",
            f"final rows exported: {counts['final_rows_exported']}",
            f"remaining queued work: {counts['remaining_queued_work']}",
            f"exports directory: {output_dir}",
        ]
    )
