from pathlib import Path

import eu_startups_pipeline.pipeline as pipeline_module
from eu_startups_pipeline.config import Paths, Settings
from eu_startups_pipeline.db import Database
from eu_startups_pipeline.exporters import export_results
from eu_startups_pipeline.models import CompanyRecord
from eu_startups_pipeline.pipeline import PipelineRunner
from eu_startups_pipeline.review import apply_review_resolutions, make_review_item


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        search_url="https://www.eu-startups.com/directory/page/1/?q=berlin",
        min_delay_seconds=0,
        max_delay_seconds=0,
        request_timeout_seconds=5,
        max_retries=2,
        disable_funding_filter=False,
        refresh_search_pages=False,
        refresh_company_pages=False,
        refresh_website_pages=False,
        refresh_search_fallbacks=False,
        playwright_headless=True,
        user_agent="test-agent",
        enable_runtime_llm=False,
        openai_api_key="",
        openai_model="",
        openai_base_url="https://api.openai.com/v1",
        funding_policy={"allowed_buckets": [], "excluded_buckets": [], "bucket_ranges_eur": {}},
        target_roles={"CEO": ["ceo"], "CTO": ["cto"], "Founder": ["founder"]},
        website_probe_paths=["/"],
        paths=Paths(
            root=tmp_path,
            output_dir=tmp_path / "exports",
            state_dir=tmp_path / "state",
            raw_dir=tmp_path / "raw",
            log_dir=tmp_path / "logs",
            db_path=tmp_path / "state" / "pipeline.sqlite3",
            policy_path=tmp_path / "config" / "pipeline_policy.json",
        ),
    )


def test_company_without_people_is_queued_for_codex_review(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    runner = PipelineRunner(settings)
    try:
        runner.db.upsert_company(
            CompanyRecord(
                company_url="https://www.eu-startups.com/directory/acme-ai/",
                company_name="Acme AI",
                website_url="https://acme.example",
                total_funding_raw="€1-5 million",
                total_funding_display="€1-5 million",
                funding_bucket_key="€1-5 million",
                passes_funding_filter=True,
            )
        )

        monkeypatch.setattr(
            pipeline_module,
            "discover_company_linkedin",
            lambda **kwargs: (
                "https://www.linkedin.com/company/acme-ai/people",
                "website",
                0.95,
                2,
            ),
        )
        monkeypatch.setattr(pipeline_module, "discover_people", lambda **kwargs: [])

        runner._process_company_enrichment(
            "https://www.eu-startups.com/directory/acme-ai/", fetcher=object()
        )

        target = runner.db.next_codex_review_target()

        assert target is not None
        assert target.review_type == "company_people_research"
        assert target.company_name == "Acme AI"
        assert target.company_linkedin == "https://www.linkedin.com/company/acme-ai/people"
    finally:
        runner.close()


def test_apply_review_resolution_can_add_person_and_export(tmp_path: Path):
    db = Database(tmp_path / "pipeline.sqlite3")
    try:
        db.initialize()
        db.upsert_company(
            CompanyRecord(
                company_url="https://www.eu-startups.com/directory/acme-ai/",
                company_name="Acme AI",
                website_url="https://acme.example",
                total_funding_raw="€1-5 million",
                total_funding_display="€1-5 million",
                funding_bucket_key="€1-5 million",
                passes_funding_filter=True,
            )
        )
        db.upsert_enrichment(
            "https://www.eu-startups.com/directory/acme-ai/",
            linkedin_company_url="https://www.linkedin.com/company/acme-ai/people",
            linkedin_source="website",
            linkedin_confidence=0.95,
            website_checked=True,
            pages_scanned=2,
        )
        review_item = make_review_item(
            review_type="company_people_research",
            company_url="https://www.eu-startups.com/directory/acme-ai/",
            company_name="Acme AI",
            subject="Acme AI",
            proposed_value="",
            candidate_values=[],
            context={"website_url": "https://acme.example"},
        )
        db.upsert_review_item(review_item)

        resolutions_path = tmp_path / "review_resolutions.csv"
        resolutions_path.write_text(
            "\n".join(
                [
                    "review_id,review_type,company,company_url,subject,proposed_value,candidate_values,context,resolution_action,resolution_value,resolution_notes",
                    (
                        f"{review_item.review_id},company_people_research,Acme AI,"
                        "https://www.eu-startups.com/directory/acme-ai/,Acme AI,,,,"
                        "add_person,Jane Doe | CTO | https://www.linkedin.com/in/jane-doe,found on team page"
                    ),
                    (
                        f"{review_item.review_id},company_people_research,Acme AI,"
                        "https://www.eu-startups.com/directory/acme-ai/,Acme AI,,,,"
                        "done,,completed by Codex"
                    ),
                ]
            ),
            encoding="utf-8",
        )

        applied = apply_review_resolutions(db, resolutions_path)
        count = export_results(db, tmp_path)
        csv_text = (tmp_path / "results_clean.csv").read_text(encoding="utf-8")

        assert applied == 2
        assert count == 1
        assert "Jane Doe" in csv_text
        assert "CTO" in csv_text
        assert "https://www.linkedin.com/company/acme-ai/people" in csv_text
    finally:
        db.close()
