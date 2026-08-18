"""SSRF-resistant URL validation for public document acquisition."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from urllib.parse import urlsplit, urlunsplit


class URLSecurityError(ValueError):
    """Raised when a URL is outside the acquisition security boundary."""


Resolver = Callable[..., list[tuple[object, ...]]]


def validate_url(
    url: str,
    allowed_domains: Sequence[str],
    *,
    resolver: Resolver = socket.getaddrinfo,
) -> str:
    """Validate scheme, authority, allowlist, port, and every DNS result."""
    if not isinstance(url, str) or not url or len(url) > 2_048:
        raise URLSecurityError("URL must be a non-empty string no longer than 2048 bytes.")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise URLSecurityError("Only HTTPS URLs are allowed.")
    if parsed.username is not None or parsed.password is not None:
        raise URLSecurityError("URL credentials are not allowed.")
    if not parsed.hostname:
        raise URLSecurityError("URL must contain a hostname.")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        port = parsed.port or 443
    except (UnicodeError, ValueError) as exc:
        raise URLSecurityError("URL contains an invalid hostname or port.") from exc
    if port != 443:
        raise URLSecurityError("Only the standard HTTPS port is allowed.")
    if not _host_is_allowed(hostname, allowed_domains):
        raise URLSecurityError(f"URL hostname {hostname!r} is not in the allowlist.")

    try:
        addresses = resolver(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise URLSecurityError(f"Could not resolve allowlisted hostname {hostname!r}.") from exc
    if not addresses:
        raise URLSecurityError(f"Allowlisted hostname {hostname!r} did not resolve.")
    for result in addresses:
        try:
            sockaddr = result[4]
            address = ipaddress.ip_address(str(sockaddr[0]))  # type: ignore[index]
        except (IndexError, TypeError, ValueError) as exc:
            raise URLSecurityError("DNS returned an invalid address.") from exc
        if not address.is_global:
            raise URLSecurityError(
                f"URL hostname {hostname!r} must resolve only to public addresses."
            )

    clean_netloc = hostname
    if ":" in hostname:
        clean_netloc = f"[{hostname}]"
    return urlunsplit(("https", clean_netloc, parsed.path or "/", parsed.query, ""))


def _host_is_allowed(hostname: str, allowed_domains: Sequence[str]) -> bool:
    for raw_domain in allowed_domains:
        domain = str(raw_domain).lower().strip().lstrip(".").rstrip(".")
        if domain and (hostname == domain or hostname.endswith(f".{domain}")):
            return True
    return False
