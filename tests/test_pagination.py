from pathlib import Path

import eu_startups_pipeline.pipeline as pipeline_module
from eu_startups_pipeline.config import Paths, Settings
from eu_startups_pipeline.models import PageFetchResult
from eu_startups_pipeline.parsers import parse_search_results
from eu_startups_pipeline.pipeline import PipelineRunner


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
        target_roles={},
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


def test_search_page_duplicate_is_rechecked_before_stopping(tmp_path: Path):
    settings = _settings(tmp_path)
    runner = PipelineRunner(settings)
    try:
        page1_html = """
        <html><body>
          <h3>Search Results (30)</h3>
          <div class="wpbdp-listing-excerpt"><h3><a href="/directory/a/">A</a></h3></div>
          <a class="next-page" href="/directory/page/2/?q=berlin">Next</a>
        </body></html>
        """
        page2_dup_html = """
        <html><body>
          <h3>Search Results (30)</h3>
          <div class="wpbdp-listing-excerpt"><h3><a href="/directory/a/">A</a></h3></div>
          <a class="next-page" href="/directory/page/3/?q=berlin">Next</a>
        </body></html>
        """
        page2_good_html = """
        <html><body>
          <h3>Search Results (30)</h3>
          <div class="wpbdp-listing-excerpt"><h3><a href="/directory/b/">B</a></h3></div>
          <a class="next-page" href="/directory/page/3/?q=berlin">Next</a>
        </body></html>
        """
        page1_result = parse_search_results(
            page1_html, "https://www.eu-startups.com/directory/page/1/?q=berlin"
        )
        runner.db.record_search_page(page1_result, str(tmp_path / "page1.html"), repeated=False)

        class FakeFetcher:
            def __init__(self) -> None:
                self.calls = 0

            def fetch(
                self, url: str, *, kind: str, refresh: bool = False, prefer_browser: bool = False
            ) -> PageFetchResult:
                self.calls += 1
                html = page2_dup_html if self.calls == 1 else page2_good_html
                return PageFetchResult(
                    url=url,
                    final_url=url,
                    domain="www.eu-startups.com",
                    status_code=200,
                    content=html,
                    body_path=str(tmp_path / f"page2-{self.calls}.html"),
                    from_cache=False,
                )

        fake_fetcher = FakeFetcher()
        runner._process_search_page(
            "https://www.eu-startups.com/directory/page/2/?q=berlin", fake_fetcher, {}
        )

        stored = runner.db.conn.execute(
            "SELECT repeated, next_url FROM search_pages WHERE url = ?",
            ("https://www.eu-startups.com/directory/page/2/?q=berlin",),
        ).fetchone()
        queued = runner.db.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE task_type = 'crawl_search_page' AND url = ?",
            ("https://www.eu-startups.com/directory/page/3/?q=berlin",),
        ).fetchone()[0]

        assert fake_fetcher.calls == 2
        assert stored["repeated"] == 0
        assert stored["next_url"] == "https://www.eu-startups.com/directory/page/3/?q=berlin"
        assert queued == 1
    finally:
        runner.close()


def test_run_loop_requeues_transient_failure_instead_of_failing_permanently(
    tmp_path: Path, monkeypatch
):
    settings = _settings(tmp_path)
    runner = PipelineRunner(settings)
    try:
        runner.start_new_run(reset_state=True)

        class FakeFetcher:
            def __init__(self, settings, db) -> None:
                pass

            def fetch(
                self, url: str, *, kind: str, refresh: bool = False, prefer_browser: bool = False
            ):
                raise RuntimeError("temporary fetch problem")

            def close(self) -> None:
                return None

        monkeypatch.setattr(pipeline_module, "Fetcher", FakeFetcher)

        processed = runner.resume(max_tasks=1)
        row = runner.db.conn.execute(
            "SELECT status, last_error FROM tasks ORDER BY id LIMIT 1"
        ).fetchone()

        assert processed == 0
        assert row["status"] == "cooldown"
        assert "retrying after error" in row["last_error"]
    finally:
        runner.close()
