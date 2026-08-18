from __future__ import annotations

import io
import socket
import unittest

from src.scraper.http_client import HttpBodyLimitError, SafeHttpClient
from src.scraper.security import URLSecurityError


def public_resolver(host: str, port: int, **_: object) -> list[tuple[object, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


class FakeResponse:
    def __init__(
        self,
        status: int,
        body: bytes = b"",
        *,
        url: str = "https://example.com/resource",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._body = io.BytesIO(body)
        self._url = url
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        self._body.close()


class FakeOpener:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[object] = []

    def open(self, request: object, timeout: float) -> FakeResponse:
        self.requests.append(request)
        return self.responses.pop(0)


class SafeHttpClientTest(unittest.TestCase):
    def make_client(self, responses: list[FakeResponse]) -> SafeHttpClient:
        return SafeHttpClient(
            user_agent="TravelCrawlerTest/1.0",
            timeout_seconds=1,
            request_interval_seconds=0,
            retries=0,
            resolver=public_resolver,
            opener=FakeOpener(responses),
        )

    def test_revalidates_redirect_target_against_allowlist(self) -> None:
        client = self.make_client(
            [
                FakeResponse(
                    302,
                    headers={"Location": "https://evil.example.net/pds.pdf"},
                )
            ]
        )

        with self.assertRaisesRegex(URLSecurityError, "allowlist"):
            with client.open_stream(
                "https://example.com/pds.pdf",
                ("example.com",),
            ):
                self.fail("Unsafe redirect must not be yielded")

    def test_fetch_bytes_enforces_body_limit(self) -> None:
        client = self.make_client(
            [FakeResponse(200, b"123456", headers={"Content-Type": "text/html"})]
        )

        with self.assertRaises(HttpBodyLimitError):
            client.fetch_bytes(
                "https://example.com/page",
                ("example.com",),
                max_bytes=5,
            )

    def test_robots_is_fail_closed_on_server_error(self) -> None:
        client = self.make_client([FakeResponse(500)])

        self.assertFalse(
            client.robots_allowed("https://example.com/pds", ("example.com",))
        )

    def test_missing_robots_file_allows_collection(self) -> None:
        client = self.make_client([FakeResponse(404)])

        self.assertTrue(
            client.robots_allowed("https://example.com/pds", ("example.com",))
        )

    def test_robots_disallow_rule_is_honoured(self) -> None:
        client = self.make_client(
            [
                FakeResponse(
                    200,
                    b"User-agent: *\nDisallow: /private/\n",
                    headers={"Content-Type": "text/plain"},
                )
            ]
        )

        self.assertFalse(
            client.robots_allowed(
                "https://example.com/private/pds.pdf",
                ("example.com",),
            )
        )


if __name__ == "__main__":
    unittest.main()
