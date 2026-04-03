from __future__ import annotations

import asyncio
from urllib.parse import urljoin

from src.models import PDFLink, ScraperConfig, ScraperSourceConfig
from src.scraper.base import BaseScraper

try:
    import httpx
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - optional dependency
    httpx = None
    BeautifulSoup = None

try:
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover - optional dependency
    async_playwright = None


class StaticCrawler(BaseScraper):
    """
    Static HTML crawler powered by httpx + BeautifulSoup.
    """

    def discover_pdf_links(self, source: ScraperSourceConfig) -> list[PDFLink]:
        if httpx is None or BeautifulSoup is None:
            raise RuntimeError("Static crawling requires httpx and beautifulsoup4.")

        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            response = client.get(source.entry_url)
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        links = []
        for anchor in soup.select(source.pdf_selector):
            href = anchor.get("href")
            if not href:
                continue
            absolute_url = urljoin(source.entry_url, href)
            product_type = self._infer_product_type(anchor.get_text(" ", strip=True), source)
            links.append(
                PDFLink(
                    url=absolute_url,
                    vertical=self.config.vertical,
                    fund_code=source.fund_code,
                    product_type=product_type,
                    source_page=source.entry_url,
                    metadata={"anchor_text": anchor.get_text(" ", strip=True)},
                )
            )
        return links

    @staticmethod
    def _infer_product_type(text: str, source: ScraperSourceConfig) -> str:
        lowered = text.lower()
        for keyword, mapped_type in source.product_type_hints.items():
            if keyword.lower() in lowered:
                return mapped_type
        if "hospital" in lowered:
            return "hospital"
        if "extra" in lowered:
            return "extras"
        return "general"


class DynamicCrawler(BaseScraper):
    """
    Playwright-driven crawler for JS-rendered sites.
    """

    def discover_pdf_links(self, source: ScraperSourceConfig) -> list[PDFLink]:
        if async_playwright is None:
            raise RuntimeError("Dynamic crawling requires playwright.")
        return asyncio.run(self._discover_async(source))

    async def _discover_async(self, source: ScraperSourceConfig) -> list[PDFLink]:
        links: list[PDFLink] = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(source.entry_url, wait_until="networkidle")
            if source.wait_selector:
                await page.wait_for_selector(source.wait_selector, timeout=15000)
            anchors = await page.locator(source.pdf_selector).evaluate_all(
                """elements => elements.map(el => ({
                    href: el.href || el.getAttribute('href'),
                    text: (el.innerText || el.textContent || '').trim()
                }))"""
            )
            for anchor in anchors:
                href = anchor.get("href")
                if not href:
                    continue
                links.append(
                    PDFLink(
                        url=urljoin(source.entry_url, href),
                        vertical=self.config.vertical,
                        fund_code=source.fund_code,
                        product_type=StaticCrawler._infer_product_type(anchor.get("text", ""), source),
                        source_page=source.entry_url,
                        metadata={"anchor_text": anchor.get("text", "")},
                    )
                )
            await browser.close()
        return links


def scraper_from_config(config: ScraperConfig) -> BaseScraper:
    if config.crawl_strategy == "dynamic":
        return DynamicCrawler(config)
    return StaticCrawler(config)
