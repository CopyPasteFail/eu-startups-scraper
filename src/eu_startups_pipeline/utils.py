from __future__ import annotations

import hashlib
import json
import random
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

WHITESPACE_RE = re.compile(r"\s+")
PERSON_NAME_RE = re.compile(
    r"\b([A-Z][a-z]+(?:[-'][A-Z][a-z]+)?(?:\s+[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?){1,3})\b"
)


def utcnow() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def normalize_space(value: str) -> str:
    return WHITESPACE_RE.sub(" ", value or "").strip()


def stable_hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update((part or "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def slugify_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    return cleaned.strip("-") or "item"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, sort_keys=True)


def load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def sleep_with_jitter(min_seconds: float, max_seconds: float) -> None:
    if max_seconds <= 0:
        return
    time.sleep(random.uniform(min_seconds, max_seconds))  # nosec B311


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    clean = parsed._replace(fragment="")
    return urlunparse(clean)


def normalize_domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def absolute_url(base_url: str, maybe_relative: str) -> str:
    return canonicalize_url(urljoin(base_url, maybe_relative))


def people_tab_url(url: str) -> str:
    clean = canonicalize_url(url)
    if "linkedin.com/company/" not in clean:
        return clean
    parsed = urlparse(clean)
    path = parsed.path.rstrip("/")
    if path.endswith("/people"):
        new_path = path
    elif path.endswith("/about"):
        new_path = path[: -len("/about")] + "/people"
    else:
        new_path = path + "/people"
    return urlunparse(parsed._replace(path=new_path))


def linkedin_company_url(url: str) -> str:
    clean = canonicalize_url(url)
    if "linkedin.com/company/" not in clean:
        return clean
    parsed = urlparse(clean)
    parts = [part for part in parsed.path.split("/") if part]
    if "company" in parts:
        company_idx = parts.index("company")
        slug = parts[company_idx + 1] if len(parts) > company_idx + 1 else ""
        return urlunparse(parsed._replace(path=f"/company/{slug}", query="", fragment=""))
    return clean


def linkedin_person_url(url: str) -> str:
    clean = canonicalize_url(url)
    parsed = urlparse(clean)
    return urlunparse(parsed._replace(query="", fragment=""))


def find_possible_person_name(text: str) -> str:
    match = PERSON_NAME_RE.search(text)
    return match.group(1) if match else ""
