"""Bounded static HTML extraction with per-hop IP pinning."""

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.config import Failure
from app.utils.validators import normalize, resolve

MAX_BYTES = 2 * 1024 * 1024
# UTF-8 byte count is a conservative upper bound for byte-based BPE tokens.
PROMPT_BYTES = 8000
Confidence = Literal["low", "medium", "high"]


@dataclass
class Evidence:
    url: str
    text: str
    pages: int
    confidence: Confidence


async def fetch(
    start: httpx.URL, domain: bytes | None = None
) -> tuple[httpx.URL, bytes, bool]:
    """Keep DNS, TLS identity, redirects and streaming limits under our control."""
    url = start
    for hop in range(4):
        ip = await resolve(url)
        if domain and url.raw_host != domain:
            raise Failure(502, "external_redirect", "Secondary page left the website.")
        headers = {
            "Host": url.netloc.decode(),
            "Accept": "text/html",
            "Accept-Encoding": "identity",
            "User-Agent": "Mozilla/5.0 (compatible; Brandorgalytics/1.0)",
        }
        # Isolate pools per hop: two TLS names may share a numerical IP.
        async with (
            httpx.AsyncClient(trust_env=False, timeout=8) as client,
            client.stream(
                "GET",
                url.copy_with(host=ip),
                headers=headers,
                extensions={"sni_hostname": url.raw_host.decode()},
            ) as response,
        ):
            if response.status_code in (301, 302, 303, 307, 308):
                target = response.headers.get("location")
                if hop == 3 or not target:
                    raise Failure(
                        502,
                        "bad_redirect",
                        "Website redirects could not be followed.",
                    )
                url = normalize(urljoin(str(url), target))
                continue
            limited = response.status_code in (403, 429)
            if response.status_code != 200 and not limited:
                raise Failure(
                    502, "site_unreachable", "Website did not return a usable page."
                )
            if (
                response.headers.get("content-type", "").split(";")[0].strip().lower()
                != "text/html"
            ):
                raise Failure(502, "non_html", "Website did not return HTML.")
            if (
                response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                raise Failure(
                    502,
                    "unsupported_encoding",
                    "Website requires compressed transfer.",
                )
            data = bytearray()
            async for chunk in response.aiter_raw():
                if len(data) + len(chunk) > MAX_BYTES:
                    raise Failure(
                        502,
                        "page_too_large",
                        "Website page exceeds the byte limit.",
                    )
                data.extend(chunk)
            return url, bytes(data), limited
    raise AssertionError("redirect loop exhausted")


def parse(url: httpx.URL, data: bytes) -> tuple[list[tuple[int, str]], list[str], str]:
    soup = BeautifulSoup(data, "lxml")
    lines: list[tuple[int, str]] = []
    links: dict[str, tuple[int, str]] = {}

    def add(priority: int, label: str, value: str) -> None:
        clean = " ".join(value.split())
        if clean:
            lines.append((priority, f"{label}: {clean}"))

    for a in soup.select("a[href]"):
        label = a.get_text(" ", strip=True)
        href = str(a["href"])
        try:
            target = normalize(urljoin(str(url), href))
        except (Failure, ValueError):
            continue
        if target.raw_host == url.raw_host and target != url:
            for group, pattern in (
                ("services", "services?|products?"),
                ("about", "about"),
                ("pricing", "pricing|plans"),
            ):
                match = re.compile(r"\b(?:" + pattern + r")\b", re.IGNORECASE)
                if match.search(target.path) or match.match(label):
                    # Prefer a short section landing path over an individual product.
                    score = (1000 if match.search(target.path) else 0) - len(
                        target.path
                    )
                    if group not in links or score > links[group][0]:
                        links[group] = (score, str(target))
        elif target.raw_host != url.raw_host and label:
            add(4, "External link", f"{label} ({target.raw_host.decode()})")
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            add(
                3,
                "JSON-LD",
                json.dumps(json.loads(tag.get_text()), ensure_ascii=False)[:1500],
            )
        except (ValueError, RecursionError):
            pass
    for tag in soup.select("script, style, noscript, template, svg"):
        tag.decompose()
    for tag in soup.select("title, h1, h2, h3, nav, footer, address"):
        add(
            0 if tag.name in ("title", "h1") else 2,
            tag.name,
            tag.get_text(" ", strip=True)[:600],
        )
    for tag in soup.select('meta[name="description"], meta[property^="og:"]'):
        add(
            1
            if tag.get("name") == "description"
            or tag.get("property") == "og:description"
            else 4,
            str(tag.get("name") or tag.get("property")),
            str(tag.get("content", ""))[:600],
        )
    for tag in soup.select('link[rel~="canonical"]'):
        add(3, "Canonical", str(tag.get("href", "")))
    for tag in soup.select("nav, header, footer, head"):
        tag.decompose()
    main = soup.select_one("main, article") or soup.body or soup
    body = " ".join(main.stripped_strings)
    for paragraph in main.stripped_strings:
        add(2, "Body", paragraph[:1200])
    return lines, list(dict.fromkeys(link for _, link in links.values())), body


async def extract(raw: str) -> Evidence:
    try:
        async with asyncio.timeout(35):
            url, data, limited = await fetch(normalize(raw))
            lines, links, body = parse(url, data)
            pages = 1
            # Challenge pages often carry substantial irrelevant body copy.
            challenge = re.compile(
                r"just a moment|access denied|verify you are human|checking your browser|enable javascript and cookies to continue",
                re.IGNORECASE,
            )
            has_description = any(
                text.startswith(("description:", "og:description:"))
                for _, text in lines
            )
            if challenge.search(body[:300]) or (
                limited and not (has_description and len(body) >= 180)
            ):
                raise Failure(
                    502,
                    "no_content",
                    "Website access was blocked. No reliable content is available.",
                )
            for link in links[:3]:
                try:
                    final, extra, denied = await fetch(normalize(link), url.raw_host)
                    if denied:
                        continue
                    more, _, extra_body = parse(final, extra)
                    lines.extend(more)
                    body += " " + extra_body
                    pages += 1
                except Failure as exc:
                    if exc.status == 422:
                        raise
                except httpx.RequestError:
                    continue
            visible = " ".join(dict.fromkeys(body.split()))
            metadata = " ".join(
                text.partition(": ")[2]
                for _, text in lines
                if text.startswith(("description:", "og:description:", "JSON-LD:"))
            )
            if len(visible) < 30 and len(metadata.strip()) < 30:
                raise Failure(
                    502,
                    "no_content",
                    "Too little website content is available for analysis.",
                )
            confidence = (
                "low"
                if limited or len(visible) < 180
                else "medium"
                if len(visible) < 600
                else "high"
            )
            ordered = dict.fromkeys(
                value for _, value in sorted(lines, key=lambda pair: pair[0])
            )
            text = "\n".join(ordered).encode()[:PROMPT_BYTES].decode(errors="ignore")
            return Evidence(str(url), text, pages, confidence)
    except (TimeoutError, httpx.TimeoutException):
        raise Failure(504, "site_timeout", "Website fetch timed out.") from None
    except (httpx.RequestError, ValueError):
        raise Failure(502, "site_unreachable", "Website connection failed.") from None
