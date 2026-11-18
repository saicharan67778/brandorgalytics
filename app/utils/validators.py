"""Validate URL syntax, DNS answers and numeric connection targets."""

import asyncio
import ipaddress
import re
import socket

import httpx

from app.config import Failure


def blocked() -> Failure:
    return Failure(
        422, "blocked_url", "Use a public HTTP or HTTPS website on port 80 or 443."
    )


def public_ip(value: str) -> str:
    addr = ipaddress.ip_address(value)
    if (
        not addr.is_global
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
    ):
        raise blocked()
    if isinstance(addr, ipaddress.IPv6Address) and (
        addr.ipv4_mapped
        or addr.sixtofour
        or addr.teredo
        or addr.is_site_local
        or addr not in ipaddress.ip_network("2000::/3")
        or addr in ipaddress.ip_network("3fff::/20")
    ):
        raise blocked()
    return str(addr)


def normalize(raw: str) -> httpx.URL:
    if len(raw) > 4096 or re.search(r"[\s\\\x00-\x1f\x7f]", raw):
        raise blocked()
    try:
        url = httpx.URL(raw)
        authority = (
            raw.split("://", 1)[1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        )
        host = url.raw_host.decode().lower().rstrip(".")
        if (
            url.scheme not in ("http", "https")
            or url.port not in (None, 80, 443)
            or "@" in authority
            or "%" in authority
            or not host
        ):
            raise blocked()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if (
                "." not in host
                or host.endswith(
                    (
                        ".localhost",
                        ".local",
                        ".internal",
                        ".lan",
                        ".home",
                        ".arpa",
                        ".corp",
                        ".localdomain",
                    )
                )
                or re.fullmatch(r"(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*", host)
                or any(
                    not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in host.split(".")
                )
            ):
                raise blocked()
        else:
            public_ip(host)
        return url.copy_with(host=host, raw_path=url.raw_path, fragment=None)
    except (ValueError, IndexError, UnicodeError, httpx.InvalidURL):
        raise blocked() from None


async def resolve(url: httpx.URL) -> str:
    """Fail closed if even one DNS answer is unsafe. Return a numeric address."""
    try:
        answers = await asyncio.get_running_loop().getaddrinfo(
            url.raw_host.decode(),
            url.port or (443 if url.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        addresses = [public_ip(row[4][0]) for row in answers]
        return addresses[0]
    except (OSError, IndexError):
        raise Failure(502, "site_unreachable", "Website DNS lookup failed.") from None
