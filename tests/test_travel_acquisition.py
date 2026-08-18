from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from src.common.json_artifacts import read_artifact
from src.scraper.http_client import FetchedBytes
from src.scraper.travel import load_acquisition_config, run_travel_acquisition


HTML = b"""
<html><body>
  <h2>Current International Comprehensive Annual Multi-Trip</h2>
  <a href="/current-pds.pdf">Travel Insurance Product Disclosure Statement</a>
  <a href="/alias-pds.pdf">Alternate link to Travel Insurance PDS</a>
  <a href="/current-spds.pdf">Supplementary Product Disclosure Statement</a>
  <a href="/benefits.pdf">Travel Insurance Benefits Summary</a>
</body></html>
"""


class FakeStream:
    def __init__(self, url: str, body: bytes) -> None:
        self.final_url = url
        self.status = 200
        self.headers = {
            "Content-Type": "application/pdf",
            "Content-Length": str(len(body)),
        }
        self._body = body

    def iter_chunks(self, chunk_size: int = 64 * 1024):
        yield self._body


class FakeHttpClient:
    def __init__(self) -> None:
        self.pdf_bodies = {
            "https://example.com/current-pds.pdf": b"%PDF-1.7\npds synthetic body",
            "https://example.com/alias-pds.pdf": b"%PDF-1.7\npds synthetic body",
            "https://example.com/current-spds.pdf": b"%PDF-1.7\nspds synthetic body",
            "https://example.com/benefits.pdf": b"%PDF-1.7\nbenefits synthetic body",
        }

    def robots_allowed(self, url: str, allowed_domains: tuple[str, ...]) -> bool:
        return True

    def fetch_bytes(
        self,
        url: str,
        allowed_domains: tuple[str, ...],
        *,
        max_bytes: int,
        accept: str = "",
    ) -> FetchedBytes:
        return FetchedBytes(
            final_url=url,
            status=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=HTML,
        )

    @contextmanager
    def open_stream(
        self,
        url: str,
        allowed_domains: tuple[str, ...],
        *,
        accept: str = "",
    ):
        yield FakeStream(url, self.pdf_bodies[url])


def write_config(
    path: Path,
    *,
    duplicate_provider: bool = False,
    seed_document: bool = False,
) -> None:
    provider = {
        "insurer_code": "example",
        "brand_code": "example",
        "issuer": "Example Services Pty Ltd",
        "underwriter": "Example Insurance Limited",
        "allowed_domains": ["example.com"],
        "start_pages": [
            {
                "url": "https://example.com/documents",
                "required_context_hints": [],
                "follow_link_hints": [],
            }
        ],
        "seed_documents": (
            [
                {
                    "url": "https://example.com/current-pds.pdf",
                    "source_page": "https://example.com/documents",
                    "title": "Travel Insurance Product Disclosure Statement",
                    "section_heading": "Current documents",
                    "version_status": "current"
                }
            ]
            if seed_document
            else []
        ),
        "default_axes": {
            "geographic_scopes": ["unknown"],
            "trip_frequencies": ["unknown"],
            "plan_tiers": ["unknown"],
        },
    }
    payload = {
        "vertical": "travel_insurance",
        "contract_version": "1.0.0",
        "user_agent": "TravelCrawlerTest/1.0",
        "timeout_seconds": 2,
        "request_interval_seconds": 0,
        "retries": 0,
        "max_redirects": 5,
        "max_html_bytes": 100000,
        "max_pdf_bytes": 100000,
        "max_pdf_pages": 500,
        "sample_pdf_pages": 5,
        "providers": [provider, dict(provider)] if duplicate_provider else [provider],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class TravelAcquisitionTest(unittest.TestCase):
    def test_config_rejects_duplicate_provider_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            write_config(path, duplicate_provider=True)

            with self.assertRaisesRegex(ValueError, "Duplicate insurer_code"):
                load_acquisition_config(path)

    def test_offline_run_writes_contract_valid_artifact_and_relationships(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "sources.json"
            write_config(config_path)

            outcome = run_travel_acquisition(
                config_path=config_path,
                data_root=root / "data",
                output_root=root / "outputs",
                http_client=FakeHttpClient(),
                run_id="20260811T010203Z-a1b2c3d4",
            )

            artifact = read_artifact(
                outcome.artifact_path,
                expected_type="travel_insurance_acquisition_run",
                data_contract="travel_insurance/acquisition_run",
            )
            data = artifact["data"]
            self.assertEqual(len(data["documents"]), 3)
            self.assertEqual(
                {document["document_type"] for document in data["documents"]},
                {"pds", "spds", "brochure"},
            )
            self.assertEqual(
                {relationship["relationship_type"] for relationship in data["relationships"]},
                {"supplements", "summarises"},
            )
            self.assertEqual(len(data["product_releases"]), 1)
            self.assertEqual(data["summary"]["documents_downloaded"], 3)
            self.assertEqual(len(outcome.pdf_paths), 3)
            self.assertTrue(all(path.is_file() for path in outcome.pdf_paths))
            pds = next(
                document for document in data["documents"]
                if document["document_type"] == "pds"
            )
            self.assertTrue(
                any("alternate discovery URL" in item for item in pds["evidence"])
            )

    def test_discovery_only_does_not_write_pdf_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "sources.json"
            write_config(config_path)

            outcome = run_travel_acquisition(
                config_path=config_path,
                data_root=root / "data",
                output_root=root / "outputs",
                discovery_only=True,
                http_client=FakeHttpClient(),
                run_id="20260811T010203Z-a1b2c3d4",
            )

            artifact = read_artifact(
                outcome.artifact_path,
                data_contract="travel_insurance/acquisition_run",
            )
            self.assertEqual(artifact["data"]["summary"]["documents_downloaded"], 0)
            self.assertEqual(outcome.pdf_paths, ())

    def test_official_seed_document_survives_source_page_fetch_failure(self) -> None:
        class SourcePageFailureClient(FakeHttpClient):
            def fetch_bytes(self, *args, **kwargs):
                raise RuntimeError("HTTP 403 source page")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "sources.json"
            write_config(config_path, seed_document=True)

            outcome = run_travel_acquisition(
                config_path=config_path,
                data_root=root / "data",
                output_root=root / "outputs",
                http_client=SourcePageFailureClient(),
                run_id="20260811T010203Z-a1b2c3d4",
            )

            self.assertEqual(outcome.data["summary"]["documents_downloaded"], 1)
            self.assertEqual(outcome.data["documents"][0]["document_type"], "pds")
            self.assertTrue(outcome.pdf_paths[0].is_file())


if __name__ == "__main__":
    unittest.main()
