from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote_plus

import requests
from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright

from .config import Settings
from .db import Database
from .models import PageFetchResult
from .utils import (
    ensure_parent,
    normalize_domain,
    sleep_with_jitter,
    slugify_filename,
    stable_hash,
)

LOGGER = logging.getLogger(__name__)


class CooldownError(RuntimeError):
    def __init__(self, domain: str, seconds: int, message: str) -> None:
        super().__init__(message)
        self.domain = domain
        self.seconds = seconds


class ChallengeBlockedError(RuntimeError):
    def __init__(self, domain: str, seconds: int, message: str) -> None:
        super().__init__(message)
        self.domain = domain
        self.seconds = seconds


class Fetcher:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": settings.user_agent})
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    def close(self) -> None:
        self.session.close()
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def fetch(
        self, url: str, *, kind: str, refresh: bool = False, prefer_browser: bool = False
    ) -> PageFetchResult:
        cached = self.db.get_cached_fetch(url)
        if cached and not refresh and Path(cached["body_path"]).exists():
            return PageFetchResult(
                url=url,
                final_url=cached["final_url"],
                domain=cached["domain"],
                status_code=int(cached["status_code"]),
                content=Path(cached["body_path"]).read_text(encoding="utf-8"),
                body_path=cached["body_path"],
                from_cache=True,
            )
        domain = normalize_domain(url)
        sleep_with_jitter(self.settings.min_delay_seconds, self.settings.max_delay_seconds)
        result = (
            self._browser_fetch(url, kind)
            if prefer_browser or domain.endswith("eu-startups.com")
            else self._request_fetch(url, kind)
        )
        if (
            result.status_code in {403, 406}
            and not prefer_browser
            and not domain.endswith("eu-startups.com")
        ):
            LOGGER.info("request fetch blocked for %s; retrying in browser", url)
            result = self._browser_fetch(url, kind)
        self.db.upsert_fetch_cache(
            url, result.domain, kind, result.status_code, result.final_url, result.body_path
        )
        return result

    def search_duckduckgo(self, query: str, *, refresh: bool = False) -> PageFetchResult:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        return self.fetch(url, kind="search_html", refresh=refresh, prefer_browser=False)

    def _request_fetch(self, url: str, kind: str) -> PageFetchResult:
        response = self.session.get(
            url, timeout=self.settings.request_timeout_seconds, allow_redirects=True
        )
        if response.status_code == 429:
            raise CooldownError(normalize_domain(url), 900, f"429 for {url}")
        body_path = self._write_body(url, response.text, kind)
        return PageFetchResult(
            url=url,
            final_url=str(response.url),
            domain=normalize_domain(str(response.url) or url),
            status_code=response.status_code,
            content=response.text,
            body_path=str(body_path),
            from_cache=False,
        )

    def _browser_fetch(self, url: str, kind: str) -> PageFetchResult:
        context = self._ensure_context()
        page = context.new_page()
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.settings.request_timeout_seconds * 1000,
            )
            self._wait_for_target_dom(page, url)
            status_code = response.status if response else 0
            if status_code == 429:
                raise CooldownError(normalize_domain(url), 900, f"429 for {url}")
            content = page.content()
            final_url = page.url
            if self._looks_like_cloudflare_challenge(content):
                raise ChallengeBlockedError(
                    normalize_domain(final_url or url),
                    120,
                    f"Cloudflare challenge still active for {url}",
                )
        finally:
            page.close()
        body_path = self._write_body(url, content, kind)
        return PageFetchResult(
            url=url,
            final_url=final_url,
            domain=normalize_domain(final_url or url),
            status_code=status_code,
            content=content,
            body_path=str(body_path),
            from_cache=False,
        )

    def _write_body(self, url: str, content: str, kind: str) -> Path:
        domain = normalize_domain(url) or "unknown"
        hashed = stable_hash(url)
        path = self.settings.paths.raw_dir / domain / f"{kind}-{slugify_filename(hashed[:16])}.html"
        ensure_parent(path)
        path.write_text(content, encoding="utf-8")
        return path

    def _ensure_browser(self) -> Browser:
        if self._browser is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=self.settings.playwright_headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
        return self._browser

    def _ensure_context(self) -> BrowserContext:
        if self._context is None:
            browser = self._ensure_browser()
            self._context = browser.new_context(
                user_agent=self.settings.user_agent,
                locale="en-US",
                timezone_id="Europe/Berlin",
                viewport={"width": 1440, "height": 900},
            )
            self._context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'language', { get: () => 'en-US' });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                window.chrome = window.chrome || { runtime: {} };
                """
            )
        return self._context

    def _wait_for_target_dom(self, page, url: str) -> None:
        selector = ".wpbdp-listing-excerpt, .wpbdp-field-display, h1.entry-title, h1"
        try:
            page.wait_for_selector(selector, timeout=15_000)
            return
        except Exception as exc:
            LOGGER.debug("initial selector wait failed for %s: %s", url, exc)
        try:
            title = page.title()
        except Exception:
            title = ""
        if "Just a moment" in title:
            LOGGER.info(
                "cloudflare challenge detected for %s; waiting for verification to complete", url
            )
            deadline_ms = max(60_000, self.settings.request_timeout_seconds * 1000)
            elapsed = 0
            while elapsed < deadline_ms:
                try:
                    page.wait_for_selector(selector, timeout=5_000)
                    return
                except Exception:
                    elapsed += 5_000
                    continue
            LOGGER.info("cloudflare wait timed out for %s; saving current DOM anyway", url)
            return
        LOGGER.info("selector wait timed out for %s; saving current DOM anyway", url)

    @staticmethod
    def _looks_like_cloudflare_challenge(content: str) -> bool:
        lowered = content.lower()
        return "performing security verification" in lowered or "<title>just a moment..." in lowered
