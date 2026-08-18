from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path

from src.scraper.crawler import discover_pdf_links
from src.scraper.downloader import PdfStore, PdfValidationError
from src.scraper.security import URLSecurityError, validate_url


def public_resolver(host: str, port: int, **_: object) -> list[tuple[object, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


class UrlSecurityTest(unittest.TestCase):
    def test_rejects_non_https_urls(self) -> None:
        with self.assertRaisesRegex(URLSecurityError, "HTTPS"):
            validate_url(
                "http://documents.example.com/pds.pdf",
                ("example.com",),
                resolver=public_resolver,
            )

    def test_rejects_hosts_outside_allowlist(self) -> None:
        with self.assertRaisesRegex(URLSecurityError, "allowlist"):
            validate_url(
                "https://evil.example.net/pds.pdf",
                ("example.com",),
                resolver=public_resolver,
            )

    def test_allows_a_subdomain_of_an_allowlisted_domain(self) -> None:
        validated = validate_url(
            "https://documents.example.com/pds.pdf",
            ("example.com",),
            resolver=public_resolver,
        )

        self.assertEqual(validated, "https://documents.example.com/pds.pdf")

    def test_rejects_private_dns_results(self) -> None:
        def private_resolver(
            host: str,
            port: int,
            **_: object,
        ) -> list[tuple[object, ...]]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

        with self.assertRaisesRegex(URLSecurityError, "public"):
            validate_url(
                "https://documents.example.com/pds.pdf",
                ("example.com",),
                resolver=private_resolver,
            )

    def test_rejects_credentials_in_url(self) -> None:
        with self.assertRaisesRegex(URLSecurityError, "credentials"):
            validate_url(
                "https://user:password@example.com/pds.pdf",
                ("example.com",),
                resolver=public_resolver,
            )


class StaticHtmlDiscoveryTest(unittest.TestCase):
    def test_discovers_relative_pdf_and_retains_heading_context(self) -> None:
        html = """
        <h2>Travel Insurance</h2>
        <a href="/files/current-pds.pdf">Current Combined FSG and PDS</a>
        """

        links = discover_pdf_links(
            html,
            "https://www.example.com/policy-documents",
            required_context_hints=("travel insurance",),
        )

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].url, "https://www.example.com/files/current-pds.pdf")
        self.assertEqual(links[0].section_heading, "Travel Insurance")
        self.assertEqual(links[0].anchor_text, "Current Combined FSG and PDS")
        self.assertEqual(links[0].version_status, "current")

    def test_skips_archived_section_by_default(self) -> None:
        html = """
        <h2>Current plans</h2><a href="current.pdf">Current PDS</a>
        <h2>Policies issued prior to 2024</h2><a href="old.pdf">Old PDS</a>
        """

        links = discover_pdf_links(html, "https://example.com/pds")

        self.assertEqual([link.url for link in links], ["https://example.com/current.pdf"])

    def test_includes_archived_section_when_requested(self) -> None:
        html = """
        <h2>Policies issued prior to 2024</h2><a href="old.pdf">Old PDS</a>
        """

        links = discover_pdf_links(
            html,
            "https://example.com/pds",
            include_archived=True,
        )

        self.assertEqual(links[0].version_status, "archived")

    def test_deduplicates_url_and_combines_evidence(self) -> None:
        html = """
        <h2>Comprehensive</h2><a href="combined.pdf">PDS</a>
        <h2>Basic</h2><a href="combined.pdf">PDS</a>
        """

        links = discover_pdf_links(html, "https://example.com/pds")

        self.assertEqual(len(links), 1)
        self.assertIn("heading: Comprehensive", links[0].evidence)
        self.assertIn("heading: Basic", links[0].evidence)

    def test_does_not_treat_a_pds_landing_page_as_a_pdf(self) -> None:
        html = '<h2>Policy documents</h2><a href="/pds">Combined FSG/PDS</a>'

        links = discover_pdf_links(html, "https://example.com/documents")

        self.assertEqual(links, [])

    def test_parent_archive_heading_applies_to_nested_subheadings(self) -> None:
        html = """
        <h2>Got a policy that was issued before the ones above?</h2>
        <h3>For policies issued between 2023 and 2025</h3>
        <a href="old.pdf">Comprehensive</a>
        """

        links = discover_pdf_links(html, "https://example.com/pds")

        self.assertEqual(links, [])

    def test_retains_body_text_that_follows_the_document_link(self) -> None:
        html = """
        <h3>Business plan</h3>
        <a href="business.pdf">Business PDS</a>
        <p>This plan is available for Annual multi-trip policies.</p>
        """

        links = discover_pdf_links(html, "https://example.com/pds")

        self.assertIn("Annual multi-trip policies", links[0].context_text)


class PdfStoreTest(unittest.TestCase):
    def test_streams_valid_pdf_and_returns_hash_addressed_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PdfStore(Path(tmp), max_bytes=1024)

            stored = store.store(
                [b"%PDF-1.7\n", b"synthetic-test-body"],
                insurer_code="example",
                document_type="pds",
                title="Current Travel PDS",
                content_type="application/pdf",
            )

            self.assertEqual(stored.retrieval_status, "downloaded")
            self.assertEqual(stored.validation_status, "valid_pdf")
            self.assertTrue(stored.path.is_file())
            self.assertTrue(stored.path.name.startswith(stored.sha256[:12]))
            self.assertTrue(stored.path.read_bytes().startswith(b"%PDF-"))

    def test_rejects_html_disguised_as_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PdfStore(Path(tmp), max_bytes=1024)

            with self.assertRaises(PdfValidationError) as caught:
                store.store(
                    [b"<html>blocked</html>"],
                    insurer_code="example",
                    document_type="pds",
                    title="PDS",
                    content_type="application/pdf",
                )

            self.assertEqual(caught.exception.code, "invalid_pdf_signature")
            self.assertEqual(caught.exception.validation_status, "not_pdf")
            self.assertEqual(list(Path(tmp).rglob("*.pdf")), [])

    def test_enforces_streaming_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PdfStore(Path(tmp), max_bytes=10)

            with self.assertRaises(PdfValidationError) as caught:
                store.store(
                    [b"%PDF-", b"123456"],
                    insurer_code="example",
                    document_type="pds",
                    title="PDS",
                    content_type="application/pdf",
                )

            self.assertEqual(caught.exception.code, "pdf_size_exceeded")
            self.assertEqual(caught.exception.validation_status, "size_exceeded")

    def test_exact_rerun_is_reported_as_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PdfStore(Path(tmp), max_bytes=1024)
            kwargs = {
                "insurer_code": "example",
                "document_type": "pds",
                "title": "PDS",
                "content_type": "application/pdf",
            }

            first = store.store([b"%PDF-1.7\nbody"], **kwargs)
            second = store.store([b"%PDF-1.7\nbody"], **kwargs)

            self.assertEqual(first.path, second.path)
            self.assertEqual(second.retrieval_status, "duplicate")
            self.assertEqual(len(list(Path(tmp).rglob("*.pdf"))), 1)


if __name__ == "__main__":
    unittest.main()
