"""Coordinate per-process credit controls, extraction and report generation."""

import asyncio
import time
from collections import OrderedDict, deque

from app.config import Failure, Settings
from app.schemas import Meta, Response
from app.services.llm_service import Provider, Usage
from app.services.website_extractor import extract
from app.utils.validators import normalize


class AnalysisService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = Provider(settings)
        self.cache: OrderedDict[str, tuple[float, Response]] = OrderedDict()
        self.clients: OrderedDict[str, deque[float]] = OrderedDict()
        self.locks = [asyncio.Lock() for _ in range(32)]

    def admit(self, ip: str) -> None:
        now = time.monotonic()
        while self.clients and next(iter(self.clients.values()))[-1] <= now - 60:
            self.clients.popitem(last=False)
        if ip not in self.clients and len(self.clients) >= 10000:
            raise Failure(
                429, "rate_limit", "Request capacity reached. Retry in one minute."
            )
        hits = self.clients.setdefault(ip, deque())
        while hits and hits[0] <= now - 60:
            hits.popleft()
        if len(hits) >= self.settings.rate_limit:
            raise Failure(
                429, "rate_limit", "Request limit reached. Retry in one minute."
            )
        hits.append(now)
        self.clients.move_to_end(ip)

    async def run(self, raw: str) -> Response:
        url = str(normalize(raw))
        started = time.monotonic()
        async with self.locks[hash(url) % len(self.locks)]:
            entry = self.cache.get(url)
            if entry and entry[0] > time.monotonic():
                self.cache.move_to_end(url)
                hit = entry[1].model_copy(deep=True)
                hit.meta.cached = True
                hit.meta.duration_ms = round((time.monotonic() - started) * 1000)
                hit.meta.input_tokens = hit.meta.output_tokens = 0
                hit.meta.est_cost_usd = 0.0
                return hit
            evidence = await extract(url)
            usage = Usage()
            report = await self.provider.analyze(evidence, usage, [])
            meta = Meta(
                provider=self.provider.name,
                model=self.provider.model,
                input_tokens=usage.input,
                output_tokens=usage.output,
                est_cost_usd=usage.cost(self.provider.model),
                duration_ms=round((time.monotonic() - started) * 1000),
                pages_fetched=evidence.pages,
                content_confidence=evidence.confidence,
                evidence_note="Limited website evidence. Treat inferences as uncertain."
                if evidence.confidence == "low"
                else None,
                cached=False,
            )
            result = Response(**report.model_dump(), website=evidence.url, meta=meta)
            if self.settings.cache_ttl:
                self.cache[url] = (time.monotonic() + self.settings.cache_ttl, result)
                self.cache.move_to_end(url)
                if len(self.cache) > 256:
                    self.cache.popitem(last=False)
            return result
