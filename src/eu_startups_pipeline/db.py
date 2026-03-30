from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .models import (
    CodexReviewTarget,
    CompanyRecord,
    ExportRow,
    PersonCandidate,
    ReviewItem,
    SearchListing,
    SearchPageParseResult,
)
from .utils import (
    dump_json,
    linkedin_person_url,
    load_json,
    people_tab_url,
    stable_hash,
    utcnow,
)
from .utils import (
    linkedin_company_url as normalize_linkedin_company_url,
)


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def initialize(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS metadata (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fetch_cache (
              url TEXT PRIMARY KEY,
              domain TEXT NOT NULL,
              fetch_kind TEXT NOT NULL,
              status_code INTEGER NOT NULL,
              final_url TEXT NOT NULL,
              body_path TEXT NOT NULL,
              fetched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS domain_state (
              domain TEXT PRIMARY KEY,
              cooldown_until TEXT
            );
            CREATE TABLE IF NOT EXISTS search_pages (
              url TEXT PRIMARY KEY,
              page_number INTEGER,
              results_count INTEGER,
              next_url TEXT,
              signature TEXT,
              repeated INTEGER NOT NULL DEFAULT 0,
              html_path TEXT NOT NULL,
              fetched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS companies (
              company_url TEXT PRIMARY KEY,
              company_name TEXT NOT NULL,
              search_page_url TEXT,
              based_in TEXT,
              category TEXT,
              tags TEXT,
              founded TEXT,
              description TEXT,
              website_url TEXT,
              eu_linkedin_url TEXT,
              company_status TEXT,
              total_funding_raw TEXT,
              total_funding_display TEXT,
              funding_bucket_key TEXT,
              funding_min_eur REAL,
              funding_max_eur REAL,
              funding_unknown INTEGER NOT NULL DEFAULT 0,
              funding_stage TEXT,
              passes_funding_filter INTEGER NOT NULL DEFAULT 0,
              html_path TEXT,
              parsed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS company_enrichment (
              company_url TEXT PRIMARY KEY,
              linkedin_company_url TEXT,
              linkedin_source TEXT,
              linkedin_confidence REAL,
              linkedin_needs_review INTEGER NOT NULL DEFAULT 0,
              website_checked INTEGER NOT NULL DEFAULT 0,
              pages_scanned INTEGER NOT NULL DEFAULT 0,
              notes TEXT,
              enriched_at TEXT
            );
            CREATE TABLE IF NOT EXISTS people (
              person_key TEXT PRIMARY KEY,
              company_url TEXT NOT NULL,
              person_name TEXT NOT NULL,
              person_linkedin_url TEXT,
              role_raw TEXT,
              role_normalized TEXT,
              source TEXT,
              source_url TEXT,
              confidence REAL,
              needs_review INTEGER NOT NULL DEFAULT 0,
              include_in_output INTEGER NOT NULL DEFAULT 1,
              evidence TEXT
            );
            CREATE TABLE IF NOT EXISTS review_items (
              review_id TEXT PRIMARY KEY,
              review_type TEXT NOT NULL,
              company_url TEXT NOT NULL,
              company_name TEXT NOT NULL,
              subject TEXT NOT NULL,
              proposed_value TEXT,
              candidate_values_json TEXT,
              context_json TEXT,
              status TEXT NOT NULL DEFAULT 'open',
              resolution_action TEXT,
              resolution_value TEXT,
              resolution_notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              task_type TEXT NOT NULL,
              url TEXT,
              company_url TEXT,
              domain TEXT,
              payload_json TEXT,
              unique_key TEXT UNIQUE,
              status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              not_before TEXT,
              last_error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS llm_cache (
              cache_key TEXT PRIMARY KEY,
              model TEXT NOT NULL,
              response_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def reset_state(self) -> None:
        self.conn.executescript(
            """
            DELETE FROM fetch_cache;
            DELETE FROM domain_state;
            DELETE FROM search_pages;
            DELETE FROM companies;
            DELETE FROM company_enrichment;
            DELETE FROM people;
            DELETE FROM review_items;
            DELETE FROM tasks;
            DELETE FROM metadata;
            """
        )
        self.conn.commit()

    def set_metadata(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO metadata(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_metadata(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def enqueue_task(
        self,
        task_type: str,
        *,
        url: str = "",
        company_url: str = "",
        domain: str = "",
        payload: dict[str, Any] | None = None,
        unique_key: str | None = None,
    ) -> None:
        now = utcnow()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO tasks(
              task_type, url, company_url, domain, payload_json, unique_key, status, attempts, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)
            """,
            (
                task_type,
                url,
                company_url,
                domain,
                dump_json(payload or {}),
                unique_key or stable_hash(task_type, url, company_url),
                now,
                now,
            ),
        )
        self.conn.commit()

    def reset_running_tasks(self) -> None:
        self.conn.execute(
            "UPDATE tasks SET status = 'pending', updated_at = ? WHERE status = 'running'",
            (utcnow(),),
        )
        self.conn.commit()

    def next_task(self) -> sqlite3.Row | None:
        now = utcnow()
        row = self.conn.execute(
            """
            SELECT * FROM tasks
            WHERE status IN ('pending', 'cooldown')
              AND (not_before IS NULL OR not_before <= ?)
            ORDER BY id
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        if not row:
            return None
        self.conn.execute(
            "UPDATE tasks SET status = 'running', attempts = attempts + 1, updated_at = ? WHERE id = ?",
            (utcnow(), row["id"]),
        )
        self.conn.commit()
        return self.conn.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone()

    def complete_task(self, task_id: int) -> None:
        self.conn.execute(
            "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ?", (utcnow(), task_id)
        )
        self.conn.commit()

    def postpone_task(self, task_id: int, not_before: str, message: str = "") -> None:
        self.conn.execute(
            "UPDATE tasks SET status = 'cooldown', not_before = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (not_before, message, utcnow(), task_id),
        )
        self.conn.commit()

    def fail_task(self, task_id: int, error: str) -> None:
        self.conn.execute(
            "UPDATE tasks SET status = 'failed', last_error = ?, updated_at = ? WHERE id = ?",
            (error, utcnow(), task_id),
        )
        self.conn.commit()

    def get_cached_fetch(self, url: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM fetch_cache WHERE url = ?", (url,)).fetchone()

    def upsert_fetch_cache(
        self,
        url: str,
        domain: str,
        fetch_kind: str,
        status_code: int,
        final_url: str,
        body_path: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO fetch_cache(url, domain, fetch_kind, status_code, final_url, body_path, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
              domain = excluded.domain,
              fetch_kind = excluded.fetch_kind,
              status_code = excluded.status_code,
              final_url = excluded.final_url,
              body_path = excluded.body_path,
              fetched_at = excluded.fetched_at
            """,
            (url, domain, fetch_kind, status_code, final_url, body_path, utcnow()),
        )
        self.conn.commit()

    def set_domain_cooldown(self, domain: str, cooldown_until: str) -> None:
        self.conn.execute(
            """
            INSERT INTO domain_state(domain, cooldown_until) VALUES(?, ?)
            ON CONFLICT(domain) DO UPDATE SET cooldown_until = excluded.cooldown_until
            """,
            (domain, cooldown_until),
        )
        self.conn.commit()

    def get_domain_cooldown(self, domain: str) -> str | None:
        row = self.conn.execute(
            "SELECT cooldown_until FROM domain_state WHERE domain = ?", (domain,)
        ).fetchone()
        return row["cooldown_until"] if row else None

    def seen_signature(self, signature: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM search_pages WHERE signature = ? LIMIT 1", (signature,)
        ).fetchone()
        return bool(row)

    def record_search_page(
        self, result: SearchPageParseResult, html_path: str, repeated: bool
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO search_pages(url, page_number, results_count, next_url, signature, repeated, html_path, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
              page_number = excluded.page_number,
              results_count = excluded.results_count,
              next_url = excluded.next_url,
              signature = excluded.signature,
              repeated = excluded.repeated,
              html_path = excluded.html_path,
              fetched_at = excluded.fetched_at
            """,
            (
                result.page_url,
                result.page_number,
                result.results_count,
                result.next_url,
                result.signature,
                1 if repeated else 0,
                html_path,
                utcnow(),
            ),
        )
        self.conn.commit()

    def upsert_company_stub(self, listing: SearchListing, search_page_url: str) -> None:
        self.conn.execute(
            """
            INSERT INTO companies(company_url, company_name, search_page_url, based_in, category, tags, founded, parsed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, '')
            ON CONFLICT(company_url) DO UPDATE SET
              company_name = excluded.company_name,
              search_page_url = excluded.search_page_url,
              based_in = COALESCE(NULLIF(excluded.based_in, ''), companies.based_in),
              category = COALESCE(NULLIF(excluded.category, ''), companies.category),
              tags = COALESCE(NULLIF(excluded.tags, ''), companies.tags),
              founded = COALESCE(NULLIF(excluded.founded, ''), companies.founded)
            """,
            (
                listing.company_url,
                listing.company_name,
                search_page_url,
                listing.based_in,
                listing.category,
                listing.tags,
                listing.founded,
            ),
        )
        self.conn.commit()

    def upsert_company(self, record: CompanyRecord) -> None:
        self.conn.execute(
            """
            INSERT INTO companies(
              company_url, company_name, search_page_url, based_in, category, tags, founded, description,
              website_url, eu_linkedin_url, company_status, total_funding_raw, total_funding_display,
              funding_bucket_key, funding_min_eur, funding_max_eur, funding_unknown, funding_stage,
              passes_funding_filter, html_path, parsed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_url) DO UPDATE SET
              company_name = excluded.company_name,
              search_page_url = COALESCE(NULLIF(excluded.search_page_url, ''), companies.search_page_url),
              based_in = excluded.based_in,
              category = excluded.category,
              tags = excluded.tags,
              founded = excluded.founded,
              description = excluded.description,
              website_url = excluded.website_url,
              eu_linkedin_url = excluded.eu_linkedin_url,
              company_status = excluded.company_status,
              total_funding_raw = excluded.total_funding_raw,
              total_funding_display = excluded.total_funding_display,
              funding_bucket_key = excluded.funding_bucket_key,
              funding_min_eur = excluded.funding_min_eur,
              funding_max_eur = excluded.funding_max_eur,
              funding_unknown = excluded.funding_unknown,
              funding_stage = excluded.funding_stage,
              passes_funding_filter = excluded.passes_funding_filter,
              html_path = excluded.html_path,
              parsed_at = excluded.parsed_at
            """,
            (
                record.company_url,
                record.company_name,
                record.search_page_url,
                record.based_in,
                record.category,
                record.tags,
                record.founded,
                record.description,
                record.website_url,
                record.eu_linkedin_url,
                record.company_status,
                record.total_funding_raw,
                record.total_funding_display,
                record.funding_bucket_key,
                record.funding_min_eur,
                record.funding_max_eur,
                1 if record.funding_unknown else 0,
                record.funding_stage,
                1 if record.passes_funding_filter else 0,
                record.html_path,
                utcnow(),
            ),
        )
        self.conn.commit()

    def get_company(self, company_url: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM companies WHERE company_url = ?", (company_url,)
        ).fetchone()

    def upsert_enrichment(
        self,
        company_url: str,
        *,
        linkedin_company_url: str = "",
        linkedin_source: str = "",
        linkedin_confidence: float = 0.0,
        linkedin_needs_review: bool = False,
        website_checked: bool = False,
        pages_scanned: int = 0,
        notes: str = "",
    ) -> None:
        normalized_linkedin = (
            people_tab_url(normalize_linkedin_company_url(linkedin_company_url))
            if linkedin_company_url
            else ""
        )
        self.conn.execute(
            """
            INSERT INTO company_enrichment(
              company_url, linkedin_company_url, linkedin_source, linkedin_confidence, linkedin_needs_review,
              website_checked, pages_scanned, notes, enriched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_url) DO UPDATE SET
              linkedin_company_url = excluded.linkedin_company_url,
              linkedin_source = excluded.linkedin_source,
              linkedin_confidence = excluded.linkedin_confidence,
              linkedin_needs_review = excluded.linkedin_needs_review,
              website_checked = excluded.website_checked,
              pages_scanned = excluded.pages_scanned,
              notes = excluded.notes,
              enriched_at = excluded.enriched_at
            """,
            (
                company_url,
                normalized_linkedin,
                linkedin_source,
                linkedin_confidence,
                1 if linkedin_needs_review else 0,
                1 if website_checked else 0,
                pages_scanned,
                notes,
                utcnow(),
            ),
        )
        self.conn.commit()

    def replace_people(self, company_url: str, candidates: list[PersonCandidate]) -> None:
        self.conn.execute("DELETE FROM people WHERE company_url = ?", (company_url,))
        for candidate in candidates:
            person_key = stable_hash(
                company_url, candidate.person_name, candidate.linkedin_url, candidate.role_raw
            )
            self.conn.execute(
                """
                INSERT INTO people(
                  person_key, company_url, person_name, person_linkedin_url, role_raw, role_normalized,
                  source, source_url, confidence, needs_review, include_in_output, evidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    person_key,
                    company_url,
                    candidate.person_name,
                    candidate.linkedin_url,
                    candidate.role_raw,
                    candidate.role_normalized,
                    candidate.source,
                    candidate.source_url,
                    candidate.confidence,
                    1 if candidate.needs_review else 0,
                    1 if candidate.include_in_output else 0,
                    candidate.evidence,
                ),
            )
        self.conn.commit()

    def upsert_review_item(self, item: ReviewItem) -> None:
        now = utcnow()
        self.conn.execute(
            """
            INSERT INTO review_items(
              review_id, review_type, company_url, company_name, subject, proposed_value,
              candidate_values_json, context_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            ON CONFLICT(review_id) DO UPDATE SET
              review_type = excluded.review_type,
              company_url = excluded.company_url,
              company_name = excluded.company_name,
              subject = excluded.subject,
              proposed_value = excluded.proposed_value,
              candidate_values_json = excluded.candidate_values_json,
              context_json = excluded.context_json,
              updated_at = excluded.updated_at
            """,
            (
                item.review_id,
                item.review_type,
                item.company_url,
                item.company_name,
                item.subject,
                item.proposed_value,
                dump_json(item.candidate_values),
                dump_json(item.context),
                now,
                now,
            ),
        )
        self.conn.commit()

    def get_open_review_items(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM review_items WHERE status = 'open' ORDER BY created_at, review_id"
            )
        )

    def close_review_items(self, review_type: str, company_url: str) -> None:
        self.conn.execute(
            """
            UPDATE review_items
            SET status = 'closed', updated_at = ?
            WHERE review_type = ? AND company_url = ? AND status = 'open'
            """,
            (utcnow(), review_type, company_url),
        )
        self.conn.commit()

    def next_codex_review_target(self) -> CodexReviewTarget | None:
        row = self.conn.execute(
            """
            SELECT
              r.review_id,
              r.review_type,
              r.company_name,
              r.company_url,
              r.subject,
              r.proposed_value,
              r.candidate_values_json,
              r.context_json,
              c.website_url,
              c.total_funding_display,
              c.total_funding_raw,
              c.funding_stage,
              e.linkedin_company_url
            FROM review_items r
            LEFT JOIN companies c ON c.company_url = r.company_url
            LEFT JOIN company_enrichment e ON e.company_url = r.company_url
            WHERE r.status = 'open'
            ORDER BY r.created_at, r.review_id
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        return CodexReviewTarget(
            review_id=row["review_id"],
            review_type=row["review_type"],
            company_name=row["company_name"],
            company_url=row["company_url"],
            website_url=row["website_url"] or "",
            company_linkedin=row["linkedin_company_url"] or "",
            total_funding=row["total_funding_display"] or row["total_funding_raw"] or "",
            funding_stage=row["funding_stage"] or "",
            subject=row["subject"],
            proposed_value=row["proposed_value"] or "",
            candidate_values=load_json(row["candidate_values_json"], []),
            context=load_json(row["context_json"], {}),
        )

    def apply_review_resolution(self, review_id: str, action: str, value: str, notes: str) -> None:
        row = self.conn.execute(
            "SELECT * FROM review_items WHERE review_id = ?", (review_id,)
        ).fetchone()
        if not row:
            return
        now = utcnow()
        self.conn.execute(
            """
            UPDATE review_items
            SET status = 'applied', resolution_action = ?, resolution_value = ?, resolution_notes = ?, updated_at = ?
            WHERE review_id = ?
            """,
            (action, value, notes, now, review_id),
        )
        if row["review_type"] == "linkedin_company":
            current = self.conn.execute(
                "SELECT * FROM company_enrichment WHERE company_url = ?", (row["company_url"],)
            ).fetchone()
            self.upsert_enrichment(
                row["company_url"],
                linkedin_company_url=value
                if action == "set"
                else (current["linkedin_company_url"] if current else ""),
                linkedin_source="review_resolution",
                linkedin_confidence=1.0 if action == "set" else 0.0,
                linkedin_needs_review=False,
                website_checked=True,
                pages_scanned=current["pages_scanned"] if current else 0,
                notes=notes,
            )
        elif row["review_type"] == "person_include":
            include = action.lower() != "exclude"
            self.conn.execute(
                """
                UPDATE people SET include_in_output = ?, needs_review = 0
                WHERE company_url = ? AND person_name = ? AND role_raw = ?
                """,
                (1 if include else 0, row["company_url"], row["subject"], row["proposed_value"]),
            )
        elif row["review_type"] == "company_people_research":
            if action == "add_person":
                person_name, role_raw, linkedin_url = self._parse_manual_person_resolution(value)
                role_normalized = role_raw
                self.conn.execute(
                    """
                    INSERT INTO people(
                      person_key, company_url, person_name, person_linkedin_url, role_raw, role_normalized,
                      source, source_url, confidence, needs_review, include_in_output, evidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?)
                    ON CONFLICT(person_key) DO UPDATE SET
                      person_name = excluded.person_name,
                      person_linkedin_url = excluded.person_linkedin_url,
                      role_raw = excluded.role_raw,
                      role_normalized = excluded.role_normalized,
                      source = excluded.source,
                      source_url = excluded.source_url,
                      confidence = excluded.confidence,
                      include_in_output = excluded.include_in_output,
                      evidence = excluded.evidence
                    """,
                    (
                        stable_hash(row["company_url"], person_name, linkedin_url, role_raw),
                        row["company_url"],
                        person_name,
                        linkedin_person_url(linkedin_url),
                        role_raw,
                        role_normalized,
                        "codex_review",
                        row["company_url"],
                        0.95,
                        notes or value,
                    ),
                )
                self.conn.execute(
                    """
                    UPDATE review_items
                    SET status = 'open', resolution_action = ?, resolution_value = ?, resolution_notes = ?, updated_at = ?
                    WHERE review_id = ?
                    """,
                    (action, value, notes, now, review_id),
                )
            elif action in {"done", "no_match"}:
                pass
            else:
                raise ValueError(
                    f"Unsupported action `{action}` for review type `{row['review_type']}`"
                )
        self.conn.commit()

    def status_counts(self) -> dict[str, int]:
        counts = {
            "search_pages_fetched": self._scalar("SELECT COUNT(*) FROM search_pages"),
            "company_pages_parsed": self._scalar(
                "SELECT COUNT(*) FROM companies WHERE parsed_at != ''"
            ),
            "companies_passing_funding_filter": self._scalar(
                "SELECT COUNT(*) FROM companies WHERE passes_funding_filter = 1"
            ),
            "companies_enriched": self._scalar(
                "SELECT COUNT(*) FROM company_enrichment WHERE website_checked = 1 OR linkedin_company_url != ''"
            ),
            "people_found": self._scalar("SELECT COUNT(*) FROM people WHERE include_in_output = 1"),
            "ambiguous_items_in_review_queue": self._scalar(
                "SELECT COUNT(*) FROM review_items WHERE status = 'open'"
            ),
            "remaining_queued_work": self._scalar(
                "SELECT COUNT(*) FROM tasks WHERE status IN ('pending', 'cooldown', 'running')"
            ),
        }
        rows = self.build_export_rows()
        counts["fallback_company_rows_generated"] = sum(1 for row in rows if not row.person)
        counts["final_rows_exported"] = len(rows)
        return counts

    def build_export_rows(self) -> list[ExportRow]:
        companies = self.conn.execute(
            """
            SELECT c.*, e.linkedin_company_url
            FROM companies c
            LEFT JOIN company_enrichment e ON e.company_url = c.company_url
            WHERE c.passes_funding_filter = 1
            ORDER BY c.company_name COLLATE NOCASE
            """
        ).fetchall()
        rows: list[ExportRow] = []
        for company in companies:
            people = list(
                self.conn.execute(
                    """
                    SELECT * FROM people
                    WHERE company_url = ? AND include_in_output = 1 AND needs_review = 0
                    ORDER BY role_normalized, person_name
                    """,
                    (company["company_url"],),
                )
            )
            linkedin_company_url = company["linkedin_company_url"] or ""
            if people:
                for person in people:
                    rows.append(
                        ExportRow(
                            company=company["company_name"],
                            company_website=company["website_url"] or "",
                            eu_link=company["company_url"],
                            company_linkedin=linkedin_company_url,
                            person=person["person_name"],
                            person_linkedin=person["person_linkedin_url"] or "",
                            role=person["role_normalized"] or person["role_raw"] or "",
                            total_funding=company["total_funding_display"]
                            or company["total_funding_raw"]
                            or "",
                            funding_stage=company["funding_stage"] or "",
                        )
                    )
            elif linkedin_company_url:
                rows.append(
                    ExportRow(
                        company=company["company_name"],
                        company_website=company["website_url"] or "",
                        eu_link=company["company_url"],
                        company_linkedin=linkedin_company_url,
                        person="",
                        person_linkedin="",
                        role="",
                        total_funding=company["total_funding_display"]
                        or company["total_funding_raw"]
                        or "",
                        funding_stage=company["funding_stage"] or "",
                    )
                )
        return rows

    def _scalar(self, query: str, params: tuple[Any, ...] = ()) -> int:
        return int(self.conn.execute(query, params).fetchone()[0])

    @staticmethod
    def _parse_manual_person_resolution(value: str) -> tuple[str, str, str]:
        parts = [part.strip() for part in value.split("|")]
        if len(parts) < 2:
            raise ValueError(
                "add_person resolution_value must be `Name | Role` or `Name | Role | LinkedIn URL`"
            )
        person_name = parts[0]
        role_raw = parts[1]
        linkedin_url = parts[2] if len(parts) > 2 else ""
        if not person_name or not role_raw:
            raise ValueError(
                "add_person resolution_value must include both a person name and a role"
            )
        return person_name, role_raw, linkedin_url
