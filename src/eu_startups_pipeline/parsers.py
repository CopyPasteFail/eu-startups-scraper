from __future__ import annotations

import hashlib
import re

from bs4 import BeautifulSoup

from .funding import normalize_funding_bucket
from .models import CompanyRecord, SearchListing, SearchPageParseResult
from .utils import absolute_url, normalize_space

RESULTS_RE = re.compile(r"Search Results \((\d+)\)")


def _extract_label_value_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for field in soup.select(".wpbdp-field-display"):
        label_el = field.select_one(".field-label")
        if not label_el:
            continue
        label = normalize_space(label_el.get_text(" ", strip=True)).rstrip(":")
        text = normalize_space(field.get_text(" ", strip=True))
        value = text.replace(f"{label}:", "", 1).strip()
        if not value:
            link = field.select_one("a[href]")
            value = normalize_space(link.get("href", "")) if link else ""
        pairs[label] = value
    return pairs


def parse_search_results(html: str, page_url: str) -> SearchPageParseResult:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".wpbdp-listing-excerpt")
    listings: list[SearchListing] = []
    signature_parts: list[str] = []
    for card in cards:
        title_link = card.select_one("h3 a[href*='/directory/']")
        if not title_link:
            continue
        company_name = normalize_space(title_link.get_text(" ", strip=True))
        company_url = absolute_url(page_url, title_link.get("href", ""))
        signature_parts.append(company_url)
        card_text = normalize_space(card.get_text(" ", strip=True))
        listings.append(
            SearchListing(
                company_name=company_name,
                company_url=company_url,
                based_in=_extract_inline_value(card_text, "Based in"),
                category=_extract_inline_value(card_text, "Category"),
                tags=_extract_inline_value(card_text, "Tags"),
                founded=_extract_inline_value(card_text, "Founded"),
            )
        )
    next_url = _find_next_page_url(soup, page_url)
    results_count = None
    for heading in soup.find_all(["h2", "h3", "h4"]):
        text = normalize_space(heading.get_text(" ", strip=True))
        match = RESULTS_RE.search(text)
        if match:
            results_count = int(match.group(1))
            break
    page_number = _extract_page_number(page_url)
    signature = hashlib.sha1(  # noqa: S324
        "|".join(signature_parts).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()
    return SearchPageParseResult(
        page_url=page_url,
        page_number=page_number,
        results_count=results_count,
        next_url=next_url,
        signature=signature,
        listings=listings,
    )


def parse_company_page(html: str, company_url: str, funding_policy: dict) -> CompanyRecord:
    soup = BeautifulSoup(html, "html.parser")
    title = (
        soup.select_one(".wpbdp-single .listing-title")
        or soup.select_one(".wpbdp-listing-single .listing-title")
        or soup.select_one("h1.entry-title")
        or soup.select_one("h1")
    )
    company_name = normalize_space(title.get_text(" ", strip=True)) if title else ""
    pairs = _extract_label_value_pairs(soup)
    linkedin_link = soup.select_one("a[href*='linkedin.com/company/']")
    funding = normalize_funding_bucket(
        pairs.get("Total Funding", ""),
        funding_policy.get("bucket_ranges_eur", {}),
    )
    return CompanyRecord(
        company_url=company_url,
        company_name=company_name,
        category=pairs.get("Category", ""),
        description=pairs.get("Long Business Description", "")
        or pairs.get("Business Description", ""),
        based_in=pairs.get("Based in", ""),
        tags=pairs.get("Tags", ""),
        founded=pairs.get("Founded", ""),
        website_url=pairs.get("Website", ""),
        eu_linkedin_url=linkedin_link.get("href", "") if linkedin_link else "",
        company_status=pairs.get("Company Status", ""),
        total_funding_raw=pairs.get("Total Funding", ""),
        total_funding_display=funding.display_value,
        funding_bucket_key=funding.key,
        funding_min_eur=funding.min_eur,
        funding_max_eur=funding.max_eur,
        funding_unknown=not funding.is_known_bucket,
        funding_stage="",
    )


def looks_like_valid_company_page(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    title = (
        soup.select_one(".wpbdp-single .listing-title")
        or soup.select_one(".wpbdp-listing-single .listing-title")
        or soup.select_one("h1.entry-title")
    )
    field_labels = [
        normalize_space(field.get_text(" ", strip=True)).rstrip(":")
        for field in soup.select(".wpbdp-field-display .field-label")
    ]
    if not title:
        return False
    if "cookie preferences" in normalize_space(title.get_text(" ", strip=True)).lower():
        return False
    required_markers = {"Website", "Total Funding", "Category", "Based in"}
    return len(set(field_labels) & required_markers) >= 2


def _extract_inline_value(text: str, label: str) -> str:
    pattern = rf"{re.escape(label)}:\s*(.*?)(?=(?:Business Name|Category|Based in|Tags|Founded):|$)"
    match = re.search(pattern, text)
    return normalize_space(match.group(1)) if match else ""


def _extract_page_number(url: str) -> int | None:
    match = re.search(r"/page/(\d+)/", url)
    return int(match.group(1)) if match else 1


def _find_next_page_url(soup: BeautifulSoup, page_url: str) -> str | None:
    candidates = [
        soup.select_one("a.next-page[href]"),
        soup.select_one("a[rel='next'][href]"),
        soup.select_one(".wpbdp-pagination a.next-page[href]"),
    ]
    for link in candidates:
        if link and link.get("href"):
            return absolute_url(page_url, link.get("href", ""))
    for link in soup.select("a[href]"):
        text = normalize_space(link.get_text(" ", strip=True)).lower()
        rel = " ".join(_attribute_tokens(link.get("rel"))).lower()
        aria = normalize_space(link.get("aria-label", "")).lower()
        classes = " ".join(_attribute_tokens(link.get("class"))).lower()
        if "next" in {text, rel, aria} or "next-page" in classes or text.startswith("next"):
            return absolute_url(page_url, link.get("href", ""))
    return None


def _attribute_tokens(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]
