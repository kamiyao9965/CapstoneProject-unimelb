from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from src.models import DownloadResult, PDFLink, ScraperConfig, ScraperSourceConfig
from src.scraper.downloader import PDFDownloader


class BaseScraper(ABC):
    """
    Base class for vertical and provider-specific scrapers.
    """

    def __init__(self, config: ScraperConfig, downloader: PDFDownloader | None = None) -> None:
        self.config = config
        self.downloader = downloader or PDFDownloader.for_vertical(config.vertical, config.rate_limit)

    @abstractmethod
    def discover_pdf_links(self, source: ScraperSourceConfig) -> list[PDFLink]:
        """Return all product PDF links for a configured source."""

    def run(self) -> list[DownloadResult]:
        discovered: list[PDFLink] = []
        for source in self.config.sources:
            if self.config.respect_robots_txt and not self._allowed_by_robots(source.entry_url):
                continue
            discovered.extend(self.discover_pdf_links(source))
        return [self.downloader.download(link) for link in discovered]

    def _allowed_by_robots(self, url: str) -> bool:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            parser.read()
        except Exception:
            return True
        return parser.can_fetch("*", url)
