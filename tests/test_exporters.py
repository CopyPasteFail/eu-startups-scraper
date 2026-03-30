from pathlib import Path

from openpyxl import load_workbook

from eu_startups_pipeline.db import Database
from eu_startups_pipeline.exporters import export_results
from eu_startups_pipeline.models import CompanyRecord, PersonCandidate


def test_export_results(tmp_path: Path):
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
            linkedin_source="eu_startups",
            linkedin_confidence=0.9,
            website_checked=True,
            pages_scanned=1,
        )
        db.replace_people(
            "https://www.eu-startups.com/directory/acme-ai/",
            [
                PersonCandidate(
                    person_name="Jane Doe",
                    role_raw="Chief Executive Officer",
                    role_normalized="CEO",
                    linkedin_url="https://www.linkedin.com/in/jane-doe",
                    source="website",
                    source_url="https://acme.example/team",
                    confidence=0.9,
                )
            ],
        )
        count = export_results(db, tmp_path)
        assert count == 1
        assert (tmp_path / "results_clean.csv").exists()
        assert (tmp_path / "results_clean.xlsx").exists()
        csv_text = (tmp_path / "results_clean.csv").read_text(encoding="utf-8")
        assert "Person LinkedIn" in csv_text
        assert "contacted?" in csv_text
        assert "https://www.linkedin.com/in/jane-doe" in csv_text
        assert (
            "Company,EU-link,Company LinkedIn,Person,Person LinkedIn,Role,contacted?,Total funding,Funding stage"
            in csv_text
        )
    finally:
        db.close()


def test_export_results_keeps_fallback_row_and_people_tab_link(tmp_path: Path):
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
            linkedin_company_url="https://www.linkedin.com/company/acme-ai/about/",
            linkedin_source="review_resolution",
            linkedin_confidence=1.0,
            website_checked=True,
            pages_scanned=1,
        )

        count = export_results(db, tmp_path)

        assert count == 1
        csv_text = (tmp_path / "results_clean.csv").read_text(encoding="utf-8")
        assert "https://www.linkedin.com/company/acme-ai/people" in csv_text
        workbook = load_workbook(tmp_path / "results_clean.xlsx")
        try:
            sheet = workbook["results_clean"]
            assert sheet["C2"].hyperlink.target == "https://www.linkedin.com/company/acme-ai/people"
            assert sheet["D2"].value in ("", None)
            assert sheet["E2"].value in ("", None)
            assert sheet["F2"].value in ("", None)
            assert sheet["G2"].value in ("", None)
        finally:
            workbook.close()
    finally:
        db.close()
