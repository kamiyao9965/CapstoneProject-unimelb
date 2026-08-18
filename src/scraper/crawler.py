"""Static HTML link discovery with source-context preservation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit


_ARCHIVE_HINT = re.compile(
    r"\b(?:archive|archived|previous|prior|superseded|before|pre[- ]?\d{4})\b",
    re.IGNORECASE,
)
_PDF_HINT = re.compile(
    r"\.pdf(?:$|[?#])|\b(?:pdf|pds|spds|tmd|fsg)\b|"
    r"product disclosure|policy wording|benefit(?:s)? summary|brochure",
    re.IGNORECASE,
)


@dataclass
class DiscoveredLink:
    """A document candidate and the HTML evidence that produced it."""

    url: str
    source_page: str
    anchor_text: str
    section_heading: str | None
    version_status: str
    evidence: list[str] = field(default_factory=list)
    context_evidence: list[str] = field(default_factory=list)

    @property
    def context_text(self) -> str:
        return " ".join(
            part
            for part in (
                self.section_heading,
                self.anchor_text,
                self.url,
                *self.context_evidence,
            )
            if part
        )


@dataclass(frozen=True)
class PageLink:
    href: str
    anchor_text: str
    section_heading: str | None
    section_text: str


class _ContextLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[PageLink] = []
        self.current_heading: str | None = None
        self._heading_levels: dict[int, str] = {}
        self._section_parts: dict[str, list[str]] = {}
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []
        self._anchor_href: str | None = None
        self._anchor_parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lower_tag = tag.lower()
        if lower_tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_tag = lower_tag
            self._heading_parts = []
        elif lower_tag == "a":
            values = dict(attrs)
            self._anchor_href = values.get("href")
            self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        if self._heading_tag is not None:
            self._heading_parts.append(data)
        elif self.current_heading is not None:
            self._section_parts.setdefault(self.current_heading, []).append(data)
        if self._anchor_href is not None:
            self._anchor_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lower_tag = tag.lower()
        if lower_tag == self._heading_tag:
            heading = _normalise_space(" ".join(self._heading_parts))
            if heading:
                level = int(lower_tag[1])
                for existing_level in tuple(self._heading_levels):
                    if existing_level >= level:
                        del self._heading_levels[existing_level]
                self._heading_levels[level] = heading
                self.current_heading = " > ".join(
                    self._heading_levels[item]
                    for item in sorted(self._heading_levels)
                )
            self._heading_tag = None
            self._heading_parts = []
        elif lower_tag == "a" and self._anchor_href is not None:
            self.links.append(
                PageLink(
                    href=self._anchor_href,
                    anchor_text=_normalise_space(" ".join(self._anchor_parts)),
                    section_heading=self.current_heading,
                    section_text="",
                )
            )
            self._anchor_href = None
            self._anchor_parts = []


def parse_page_links(html: str) -> list[PageLink]:
    parser = _ContextLinkParser()
    parser.feed(html)
    parser.close()
    return [
        PageLink(
            href=link.href,
            anchor_text=link.anchor_text,
            section_heading=link.section_heading,
            section_text=_normalise_space(
                " ".join(parser._section_parts.get(link.section_heading or "", []))
            ),
        )
        for link in parser.links
    ]


def discover_pdf_links(
    html: str,
    page_url: str,
    *,
    required_context_hints: tuple[str, ...] = (),
    include_archived: bool = False,
) -> list[DiscoveredLink]:
    """Extract relevant PDF candidates from one already-fetched HTML page."""
    discovered: dict[str, DiscoveredLink] = {}
    hints = tuple(hint.lower().strip() for hint in required_context_hints if hint.strip())
    for page_link in parse_page_links(html):
        if not page_link.href or page_link.href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute_url = _normalise_url(urljoin(page_url, page_link.href))
        if ".pdf" not in urlsplit(absolute_url).path.lower():
            continue
        context = " ".join(
            part
            for part in (
                page_link.section_heading,
                page_link.anchor_text,
                absolute_url,
            )
            if part
        )
        if not _PDF_HINT.search(context):
            continue
        if hints and not any(hint in context.lower() for hint in hints):
            continue
        archived = bool(_ARCHIVE_HINT.search(context))
        if archived and not include_archived:
            continue
        evidence = [f"anchor: {page_link.anchor_text}"]
        if page_link.section_heading:
            evidence.append(f"heading: {page_link.section_heading}")
        context_evidence = [page_link.section_text[:1_000]] if page_link.section_text else []
        if context_evidence:
            evidence.append(f"context: {context_evidence[0][:500]}")
        existing = discovered.get(absolute_url)
        if existing is not None:
            for item in evidence:
                if item not in existing.evidence:
                    existing.evidence.append(item)
            for item in context_evidence:
                if item not in existing.context_evidence:
                    existing.context_evidence.append(item)
            if existing.version_status == "archived" and not archived:
                existing.version_status = "current"
            continue
        discovered[absolute_url] = DiscoveredLink(
            url=absolute_url,
            source_page=page_url,
            anchor_text=page_link.anchor_text,
            section_heading=page_link.section_heading,
            version_status="archived" if archived else "current",
            evidence=evidence,
            context_evidence=context_evidence,
        )
    return list(discovered.values())


def discover_follow_links(
    html: str,
    page_url: str,
    *,
    follow_link_hints: tuple[str, ...],
) -> list[str]:
    """Return de-duplicated non-PDF pages matching configured one-hop hints."""
    hints = tuple(hint.lower().strip() for hint in follow_link_hints if hint.strip())
    found: list[str] = []
    for page_link in parse_page_links(html):
        absolute_url = _normalise_url(urljoin(page_url, page_link.href))
        context = " ".join(
            part for part in (page_link.anchor_text, page_link.section_heading, absolute_url) if part
        ).lower()
        if hints and any(hint in context for hint in hints) and ".pdf" not in urlsplit(absolute_url).path.lower():
            if absolute_url not in found:
                found.append(absolute_url)
    return found


def _normalise_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalise_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
