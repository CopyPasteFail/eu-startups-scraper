from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .config import Settings
from .fetching import Fetcher
from .models import LinkedinCandidate, PersonCandidate
from .review import make_review_item
from .utils import (
    absolute_url,
    find_possible_person_name,
    linkedin_company_url,
    linkedin_person_url,
    normalize_space,
    people_tab_url,
)

LOGGER = logging.getLogger(__name__)
ROLE_WORDS = (
    "ceo",
    "chief executive",
    "cto",
    "chief technology",
    "head of engineering",
    "vp",
    "founder",
    "co-founder",
    "cofounder",
    "r&d",
)


def discover_company_linkedin(
    *,
    company_name: str,
    company_url: str,
    website_url: str,
    eu_linkedin_url: str,
    settings: Settings,
    fetcher: Fetcher,
    db,
) -> tuple[str, str, float, int]:
    pages_scanned = 0
    candidates: list[LinkedinCandidate] = []
    if eu_linkedin_url:
        return (
            people_tab_url(linkedin_company_url(eu_linkedin_url)),
            "eu_startups",
            0.98,
            pages_scanned,
        )
    if website_url:
        website_candidates, pages_scanned = discover_website_linkedin_candidates(
            website_url, settings, fetcher
        )
        candidates.extend(website_candidates)
    if not candidates:
        candidates.extend(
            search_linkedin_company_candidates(company_name, website_url, fetcher, settings)
        )
    if not candidates:
        return "", "", 0.0, pages_scanned
    if len(candidates) == 1:
        only = candidates[0]
        return (
            people_tab_url(linkedin_company_url(only.url)),
            only.source_kind,
            only.confidence,
            pages_scanned,
        )
    best = sorted(candidates, key=lambda item: item.confidence, reverse=True)[0]
    if best.confidence >= 0.9:
        return (
            people_tab_url(linkedin_company_url(best.url)),
            best.source_kind,
            best.confidence,
            pages_scanned,
        )
    review_item = make_review_item(
        review_type="linkedin_company",
        company_url=company_url,
        company_name=company_name,
        subject=company_name,
        proposed_value=best.url,
        candidate_values=[candidate.url for candidate in candidates[:5]],
        context={
            "website_url": website_url,
            "evidence": " | ".join(candidate.evidence for candidate in candidates[:3]),
        },
    )
    db.upsert_review_item(review_item)
    return (
        people_tab_url(linkedin_company_url(best.url)),
        "review_candidate",
        best.confidence,
        pages_scanned,
    )


def discover_website_linkedin_candidates(
    website_url: str, settings: Settings, fetcher: Fetcher
) -> tuple[list[LinkedinCandidate], int]:
    candidates: list[LinkedinCandidate] = []
    pages_scanned = 0
    for path in settings.website_probe_paths:
        page_url = absolute_url(website_url, path)
        try:
            fetched = fetcher.fetch(
                page_url, kind="website_page", refresh=settings.refresh_website_pages
            )
        except Exception as exc:
            LOGGER.info("website fetch failed for %s: %s", page_url, exc)
            continue
        pages_scanned += 1
        candidates.extend(extract_linkedin_company_candidates(page_url, fetched.content))
    deduped: dict[str, LinkedinCandidate] = {}
    for candidate in candidates:
        existing = deduped.get(candidate.url)
        if existing is None or candidate.confidence > existing.confidence:
            deduped[candidate.url] = candidate
    return list(deduped.values()), pages_scanned


def extract_linkedin_company_candidates(page_url: str, html: str) -> list[LinkedinCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[LinkedinCandidate] = []
    for link in soup.select("a[href*='linkedin.com/company/']"):
        href = link.get("href", "").strip()
        if not href:
            continue
        text = normalize_space(link.get_text(" ", strip=True))
        candidates.append(
            LinkedinCandidate(
                url=people_tab_url(linkedin_company_url(href)),
                source_url=page_url,
                source_kind="website",
                confidence=0.88 if "linkedin" in text.lower() or not text else 0.83,
                evidence=text or page_url,
            )
        )
    return candidates


def discover_people(
    *,
    company_name: str,
    company_url: str,
    website_url: str,
    settings: Settings,
    fetcher: Fetcher,
    db,
) -> list[PersonCandidate]:
    people: list[PersonCandidate] = []
    if website_url:
        for path in settings.website_probe_paths:
            page_url = absolute_url(website_url, path)
            try:
                fetched = fetcher.fetch(
                    page_url, kind="website_people_page", refresh=settings.refresh_website_pages
                )
            except Exception as exc:
                LOGGER.info("website people fetch failed for %s: %s", page_url, exc)
                continue
            people.extend(
                extract_people_candidates(page_url, fetched.content, settings.target_roles)
            )
    if not people:
        people.extend(search_people_candidates(company_name, fetcher, settings))
    deduped: dict[tuple[str, str], PersonCandidate] = {}
    for candidate in people:
        key = (candidate.person_name.lower(), candidate.role_normalized.lower())
        existing = deduped.get(key)
        if existing is None or candidate.confidence > existing.confidence:
            deduped[key] = candidate
    final_people = list(deduped.values())
    for candidate in final_people:
        if candidate.needs_review:
            db.upsert_review_item(
                make_review_item(
                    review_type="person_include",
                    company_url=company_url,
                    company_name=company_name,
                    subject=candidate.person_name,
                    proposed_value=candidate.role_raw,
                    candidate_values=[candidate.role_normalized],
                    context={"source_url": candidate.source_url, "evidence": candidate.evidence},
                )
            )
    return final_people


def extract_people_candidates(
    page_url: str, html: str, target_roles: dict[str, list[str]]
) -> list[PersonCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[PersonCandidate] = []
    for anchor in soup.select("a[href*='linkedin.com/in/'], a[href*='linkedin.com/pub/']"):
        context = normalize_space(anchor.parent.get_text(" ", strip=True)) if anchor.parent else ""
        name = normalize_space(anchor.get_text(" ", strip=True)) or find_possible_person_name(
            context
        )
        if not name:
            continue
        role_raw = extract_role_text(context)
        if not role_raw:
            continue
        role_normalized, needs_review = normalize_role(role_raw, target_roles)
        candidates.append(
            PersonCandidate(
                person_name=name,
                role_raw=role_raw,
                role_normalized=role_normalized,
                linkedin_url=linkedin_person_url(anchor.get("href", "")),
                source="website_linkedin",
                source_url=page_url,
                confidence=0.92,
                evidence=context,
                needs_review=needs_review,
                include_in_output=True,
            )
        )
    for element in soup.find_all(["p", "li", "div", "span", "h2", "h3", "h4"]):
        text = normalize_space(element.get_text(" ", strip=True))
        if len(text) > 180 or not text:
            continue
        role_raw = extract_role_text(text)
        if not role_raw:
            continue
        name = find_possible_person_name(text)
        if not name:
            continue
        role_normalized, needs_review = normalize_role(role_raw, target_roles)
        candidates.append(
            PersonCandidate(
                person_name=name,
                role_raw=role_raw,
                role_normalized=role_normalized,
                linkedin_url="",
                source="website_text",
                source_url=page_url,
                confidence=0.76,
                evidence=text,
                needs_review=needs_review,
                include_in_output=True,
            )
        )
    return candidates


def search_linkedin_company_candidates(
    company_name: str, website_url: str, fetcher: Fetcher, settings: Settings
) -> list[LinkedinCandidate]:
    domain_hint = (urlparse(website_url).hostname or "").replace("www.", "") if website_url else ""
    query = f'site:linkedin.com/company "{company_name}" "{domain_hint}"'
    fetched = fetcher.search_duckduckgo(query, refresh=settings.refresh_search_fallbacks)
    return parse_search_linkedin_company_candidates(fetched.content)


def parse_search_linkedin_company_candidates(html: str) -> list[LinkedinCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[LinkedinCandidate] = []
    for anchor in soup.select("a[href*='linkedin.com/company/']"):
        href = anchor.get("href", "")
        text = normalize_space(anchor.get_text(" ", strip=True))
        candidates.append(
            LinkedinCandidate(
                url=people_tab_url(linkedin_company_url(href)),
                source_url="duckduckgo",
                source_kind="search",
                confidence=0.72 if text else 0.66,
                evidence=text,
            )
        )
    deduped: dict[str, LinkedinCandidate] = {}
    for candidate in candidates:
        deduped[candidate.url] = candidate
    return list(deduped.values())


def search_people_candidates(
    company_name: str, fetcher: Fetcher, settings: Settings
) -> list[PersonCandidate]:
    query = f'site:linkedin.com/in "{company_name}" CEO CTO founder "head of engineering"'
    fetched = fetcher.search_duckduckgo(query, refresh=settings.refresh_search_fallbacks)
    soup = BeautifulSoup(fetched.content, "html.parser")
    candidates: list[PersonCandidate] = []
    for anchor in soup.select("a[href*='linkedin.com/in/'], a[href*='linkedin.com/pub/']"):
        text = normalize_space(anchor.get_text(" ", strip=True))
        name = find_possible_person_name(text)
        role_raw = extract_role_text(text)
        if not name or not role_raw:
            continue
        role_normalized, needs_review = normalize_role(role_raw, settings.target_roles)
        candidates.append(
            PersonCandidate(
                person_name=name,
                role_raw=role_raw,
                role_normalized=role_normalized,
                linkedin_url=linkedin_person_url(anchor.get("href", "")),
                source="search",
                source_url="duckduckgo",
                confidence=0.61,
                evidence=text,
                needs_review=needs_review,
                include_in_output=True,
            )
        )
    return candidates


def extract_role_text(text: str) -> str:
    lowered = text.lower()
    if not any(word in lowered for word in ROLE_WORDS):
        return ""
    parts = re.split(r"[|,\-–/]+", text)
    for part in parts:
        if any(word in part.lower() for word in ROLE_WORDS):
            return normalize_space(part)
    return normalize_space(text)


def normalize_role(role_raw: str, target_roles: dict[str, list[str]]) -> tuple[str, bool]:
    lowered = role_raw.lower()
    matches = [
        canonical
        for canonical, aliases in target_roles.items()
        if any(alias in lowered for alias in aliases)
    ]
    if not matches:
        return role_raw, True
    if len(matches) == 1:
        return matches[0], False
    if "Founder" in matches and len(matches) > 1:
        matches = [match for match in matches if match != "Founder"]
    return matches[0], len(matches) > 1
