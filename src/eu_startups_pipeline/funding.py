from __future__ import annotations

import re

from .models import FundingInfo
from .utils import normalize_space

AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(K|M|million)?", re.IGNORECASE)


def _amount_to_eur(number: str, unit: str | None) -> float:
    value = float(number.replace(",", "."))
    unit = (unit or "").lower()
    if unit == "k":
        return value * 1_000
    if unit in {"m", "million"}:
        return value * 1_000_000
    return value


def _canonicalize_bucket_label(value: str) -> str:
    cleaned = normalize_space(value)
    if not cleaned:
        return ""
    cleaned = re.sub(r"^\s*Between\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"€\s+", "€", cleaned)
    cleaned = re.sub(r"\s*-\s*€\s*", "-€", cleaned)
    cleaned = re.sub(r"\s*-\s*", "-", cleaned)
    cleaned = re.sub(r"\bmillion\b", "million", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def normalize_funding_bucket(raw_value: str, bucket_ranges: dict[str, dict]) -> FundingInfo:
    cleaned = normalize_space(raw_value)
    if not cleaned:
        return FundingInfo("", "", "", None, None, False)
    canonical_ranges = {
        _canonicalize_bucket_label(bucket): (bucket, rule) for bucket, rule in bucket_ranges.items()
    }
    canonical_cleaned = _canonicalize_bucket_label(cleaned)
    if canonical_cleaned in canonical_ranges:
        canonical_key, rule = canonical_ranges[canonical_cleaned]
        return FundingInfo(
            raw_value=cleaned,
            display_value=canonical_key,
            key=canonical_key,
            min_eur=rule.get("min"),
            max_eur=rule.get("max"),
            is_known_bucket=True,
        )
    if "no funding" in cleaned.lower():
        return FundingInfo(cleaned, cleaned, cleaned, 0, 0, False)
    matches = AMOUNT_RE.findall(cleaned.replace("€", ""))
    if "under" in cleaned.lower() and matches:
        max_value = _amount_to_eur(matches[0][0], matches[0][1] or None)
        return FundingInfo(cleaned, cleaned, cleaned, 0, max_value, False)
    if len(matches) >= 2:
        first_unit = matches[0][1] or matches[1][1] or None
        second_unit = matches[1][1] or matches[0][1] or None
        min_value = _amount_to_eur(matches[0][0], first_unit)
        max_value = _amount_to_eur(matches[1][0], second_unit)
        return FundingInfo(cleaned, cleaned, cleaned, min_value, max_value, False)
    if matches and ("more than" in cleaned.lower() or "+" in cleaned):
        min_value = _amount_to_eur(matches[0][0], matches[0][1] or None)
        return FundingInfo(cleaned, cleaned, cleaned, min_value, None, False)
    return FundingInfo(cleaned, cleaned, cleaned, None, None, False)


def funding_bucket_allowed(bucket_key: str, policy: dict) -> bool:
    allowed = set(policy.get("allowed_buckets", []))
    excluded = set(policy.get("excluded_buckets", []))
    if bucket_key in excluded:
        return False
    if not allowed:
        return True
    return bucket_key in allowed
