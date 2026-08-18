"""Bounded HTTPS client with redirect validation, retries, and robots support."""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
import urllib.robotparser
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from src.scraper.security import Resolver, validate_url


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class HttpClientError(RuntimeError):
    """Base acquisition HTTP error."""


class HttpStatusError(HttpClientError):
    def __init__(self, status: int, url: str) -> None:
        self.status = status
        self.url = url
        super().__init__(f"HTTP {status} while fetching {_without_query(url)}")


class HttpBodyLimitError(HttpClientError):
    """Raised when a response body exceeds its configured boundary."""


class HttpProtocolError(HttpClientError):
    """Raised for an invalid redirect or malformed response."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


@dataclass
class HttpStream:
    final_url: str
    status: int
    headers: dict[str, str]
    _response: Any

    def iter_chunks(self, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        while True:
            chunk = self._response.read(chunk_size)
            if not chunk:
                return
            yield chunk


@dataclass(frozen=True)
class FetchedBytes:
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes


class SafeHttpClient:
    """Small synchronous client for public, allowlisted HTTPS resources."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 30.0,
        request_interval_seconds: float = 0.75,
        retries: int = 2,
        max_redirects: int = 5,
        resolver: Resolver = socket.getaddrinfo,
        opener: Any | None = None,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("A non-empty crawler user agent is required.")
        if timeout_seconds <= 0 or request_interval_seconds < 0:
            raise ValueError("HTTP timeouts and intervals must be bounded positive values.")
        if retries < 0 or max_redirects < 0:
            raise ValueError("Retry and redirect limits cannot be negative.")
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.request_interval_seconds = request_interval_seconds
        self.retries = retries
        self.max_redirects = max_redirects
        self.resolver = resolver
        self.opener = opener or urllib.request.build_opener(_NoRedirectHandler())
        self._last_request: dict[str, float] = {}
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser | bool] = {}

    @contextmanager
    def open_stream(
        self,
        url: str,
        allowed_domains: Sequence[str],
        *,
        accept: str = "application/pdf,application/octet-stream;q=0.8,*/*;q=0.1",
    ) -> Iterator[HttpStream]:
        current_url = url
        redirects = 0
        response: Any | None = None
        while True:
            validated_url = validate_url(
                current_url,
                allowed_domains,
                resolver=self.resolver,
            )
            response = self._open_with_retries(validated_url, accept)
            status = _response_status(response)
            if status in _REDIRECT_STATUSES:
                location = response.headers.get("Location")
                response.close()
                response = None
                if not location:
                    raise HttpProtocolError("Redirect response omitted the Location header.")
                redirects += 1
                if redirects > self.max_redirects:
                    raise HttpProtocolError(
                        f"Redirect count exceeded the configured limit {self.max_redirects}."
                    )
                current_url = urljoin(validated_url, location)
                continue
            if status >= 400:
                response.close()
                response = None
                raise HttpStatusError(status, validated_url)
            final_url = validate_url(
                str(response.geturl() or validated_url),
                allowed_domains,
                resolver=self.resolver,
            )
            headers = {str(key): str(value) for key, value in response.headers.items()}
            try:
                yield HttpStream(final_url, status, headers, response)
            finally:
                response.close()
            return

    def fetch_bytes(
        self,
        url: str,
        allowed_domains: Sequence[str],
        *,
        max_bytes: int,
        accept: str = "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.1",
    ) -> FetchedBytes:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive.")
        with self.open_stream(url, allowed_domains, accept=accept) as stream:
            content_length = _content_length(stream.headers)
            if content_length is not None and content_length > max_bytes:
                raise HttpBodyLimitError(
                    f"Response exceeded the configured {max_bytes}-byte body limit."
                )
            body = bytearray()
            for chunk in stream.iter_chunks():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise HttpBodyLimitError(
                        f"Response exceeded the configured {max_bytes}-byte body limit."
                    )
            return FetchedBytes(
                final_url=stream.final_url,
                status=stream.status,
                headers=stream.headers,
                body=bytes(body),
            )

    def robots_allowed(self, url: str, allowed_domains: Sequence[str]) -> bool:
        """Fail closed on robots errors; explicit 404/410 means no policy file."""
        parsed = urlsplit(url)
        robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
        cached = self._robots_cache.get(robots_url)
        if isinstance(cached, bool):
            return cached
        if cached is None:
            try:
                fetched = self.fetch_bytes(
                    robots_url,
                    allowed_domains,
                    max_bytes=1024 * 1024,
                    accept="text/plain,*/*;q=0.1",
                )
            except HttpStatusError as exc:
                if exc.status in {404, 410}:
                    self._robots_cache[robots_url] = True
                    return True
                self._robots_cache[robots_url] = False
                return False
            except Exception:
                self._robots_cache[robots_url] = False
                return False
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(fetched.body.decode("utf-8", errors="replace").splitlines())
            self._robots_cache[robots_url] = parser
            cached = parser
        assert isinstance(cached, urllib.robotparser.RobotFileParser)
        return cached.can_fetch(self.user_agent, url)

    def _open_with_retries(self, url: str, accept: str) -> Any:
        attempts = self.retries + 1
        last_error: BaseException | None = None
        for attempt in range(attempts):
            self._throttle(url)
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": accept,
                    "Accept-Encoding": "identity",
                },
                method="GET",
            )
            try:
                response = self.opener.open(request, timeout=self.timeout_seconds)
            except urllib.error.HTTPError as exc:
                response = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(min(0.5 * (2**attempt), 4.0))
                    continue
                raise HttpClientError(f"Network request failed for {_without_query(url)}.") from exc
            status = _response_status(response)
            if status in _RETRY_STATUSES and attempt + 1 < attempts:
                response.close()
                time.sleep(min(0.5 * (2**attempt), 4.0))
                continue
            return response
        raise HttpClientError(f"Network request failed for {_without_query(url)}.") from last_error

    def _throttle(self, url: str) -> None:
        hostname = urlsplit(url).hostname or ""
        now = time.monotonic()
        elapsed = now - self._last_request.get(hostname, 0.0)
        wait_seconds = self.request_interval_seconds - elapsed
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        self._last_request[hostname] = time.monotonic()


def _content_length(headers: dict[str, str]) -> int | None:
    raw = next(
        (value for key, value in headers.items() if key.lower() == "content-length"),
        None,
    )
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()
    return int(status)


def _without_query(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
