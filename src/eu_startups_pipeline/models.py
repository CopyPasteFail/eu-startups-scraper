from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SearchListing:
    company_name: str
    company_url: str
    based_in: str = ""
    category: str = ""
    tags: str = ""
    founded: str = ""


@dataclass(slots=True)
class SearchPageParseResult:
    page_url: str
    page_number: int | None
    results_count: int | None
    next_url: str | None
    signature: str
    listings: list[SearchListing]


@dataclass(slots=True)
class FundingInfo:
    raw_value: str
    display_value: str
    key: str
    min_eur: float | None
    max_eur: float | None
    is_known_bucket: bool


@dataclass(slots=True)
class CompanyRecord:
    company_url: str
    company_name: str
    based_in: str = ""
    category: str = ""
    tags: str = ""
    founded: str = ""
    description: str = ""
    website_url: str = ""
    eu_linkedin_url: str = ""
    company_status: str = ""
    total_funding_raw: str = ""
    total_funding_display: str = ""
    funding_bucket_key: str = ""
    funding_min_eur: float | None = None
    funding_max_eur: float | None = None
    funding_unknown: bool = False
    funding_stage: str = ""
    passes_funding_filter: bool = False
    html_path: str = ""
    search_page_url: str = ""


@dataclass(slots=True)
class PageFetchResult:
    url: str
    final_url: str
    domain: str
    status_code: int
    content: str
    body_path: str
    from_cache: bool


@dataclass(slots=True)
class LinkedinCandidate:
    url: str
    source_url: str
    source_kind: str
    confidence: float
    evidence: str = ""


@dataclass(slots=True)
class PersonCandidate:
    person_name: str
    role_raw: str
    role_normalized: str
    linkedin_url: str = ""
    source: str = ""
    source_url: str = ""
    confidence: float = 0.0
    evidence: str = ""
    needs_review: bool = False
    include_in_output: bool = True


@dataclass(slots=True)
class ReviewItem:
    review_id: str
    review_type: str
    company_url: str
    company_name: str
    subject: str
    proposed_value: str
    candidate_values: list[str] = field(default_factory=list)
    context: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ExportRow:
    company: str
    company_website: str
    eu_link: str
    company_linkedin: str
    person: str
    person_linkedin: str
    role: str
    total_funding: str
    funding_stage: str


@dataclass(slots=True)
class CodexReviewTarget:
    review_id: str
    review_type: str
    company_name: str
    company_url: str
    website_url: str
    company_linkedin: str
    total_funding: str
    funding_stage: str
    subject: str
    proposed_value: str
    candidate_values: list[str] = field(default_factory=list)
    context: dict[str, str] = field(default_factory=dict)
