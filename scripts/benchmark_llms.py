"""Run from the project root. Each provider sees the same frozen site evidence."""

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Failure, Settings
from app.services.llm_service import PRICES, SYSTEM, Provider, Usage, prompt
from app.services.website_extractor import extract

FILE = ROOT / "docs/benchmark_results.json"
SITES = {
    "small business": "https://www.londonreviewbookshop.co.uk/",
    "SaaS": "https://plausible.io/",
    "e-commerce": "https://www.peakdesign.com/",
}
SOURCES = {
    "openai": "https://developers.openai.com/api/docs/pricing",
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
    "gemini": "https://ai.google.dev/gemini-api/docs/pricing",
}
CHECKLIST = [
    "Services grounded in evidence with honest inference labels",
    "Competitors exist and compete for the same buyers (verify independently)",
    "Queries match the audience, platform and buying intent",
    "SEO actions mention specific products, markets or customer needs",
]


def save(data: dict) -> None:
    FILE.parent.mkdir(exist_ok=True)
    temp = FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(FILE)


def summarize(group: dict) -> dict:
    rows = group["runs"]
    passed = [row for row in rows if "report" in row]
    graded = [
        row["manual_score"] for row in passed if row.get("manual_score") is not None
    ]
    return {
        "completed": len(passed),
        "attempted_runs": len(rows),
        "median_success_latency_ms": statistics.median(r["duration_ms"] for r in passed)
        if len(passed) >= 2
        else None,
        "first_pass_rate": sum(r["schema_attempts"][:1] == [True] for r in rows)
        / len(rows)
        if rows
        else None,
        "final_schema_pass_rate": len(passed) / len(rows) if rows else None,
        "mean_manual_score": statistics.mean(graded) if graded else None,
        "graded_runs": len(graded),
    }


async def benchmark(runs: int) -> None:
    data = {
        "started_utc": datetime.now(UTC).isoformat(),
        "runs_per_site": runs,
        "system_prompt": SYSTEM,
        "checklist": CHECKLIST,
        "sites": {},
        "providers": {},
    }
    sites = {}
    for kind, url in SITES.items():
        try:
            started = time.perf_counter()
            site = await extract(url)
            sites[kind] = site
            data["sites"][kind] = {
                **asdict(site),
                "requested": url,
                "extraction_ms": round((time.perf_counter() - started) * 1000),
                "prompt_sha256": hashlib.sha256(prompt(site).encode()).hexdigest(),
            }
        except Failure as exc:
            data["sites"][kind] = {"requested": url, "error": exc.code}
        print(
            kind,
            data["sites"][kind].get("confidence") or data["sites"][kind].get("error"),
            flush=True,
        )
    # Keep the recorded benchmark cohort independent of the demo's selected model.
    models = {
        "openai": "gpt-5.4-mini",
        "gemini": "gemini-3.5-flash",
        "anthropic": "claude-haiku-4-5-20251001",
    }
    for name, model in models.items():
        provider = Provider(Settings(llm_provider=name, llm_model=model))
        rates = PRICES[model]
        group = {
            "model": model,
            "pricing_url": SOURCES[name],
            "pricing_retrieved": "2026-11-18",
            "usd_per_million": dict(zip(("input", "cached_input", "output"), rates)),
            "runs": [],
            "status": "NOT MEASURED: docs-based estimate",
        }
        data["providers"][name] = group
        if provider.key:
            group["status"] = "ATTEMPTED"
            for kind, site in sites.items():
                for trial in range(1, runs + 1):
                    usage = Usage()
                    attempts = []
                    started = time.perf_counter()
                    row = {"site": kind, "trial": trial, "manual_score": None}
                    try:
                        result = await provider.analyze(site, usage, attempts)
                        row["report"] = result.model_dump()
                    except Failure as exc:
                        row["error"] = exc.code
                    row.update(
                        duration_ms=round((time.perf_counter() - started) * 1000),
                        usage=asdict(usage) if attempts else None,
                        cost_usd=usage.cost(model) if attempts else None,
                        schema_attempts=attempts,
                    )
                    group["runs"].append(row)
                    save(data)
                    print(name, kind, trial, row.get("error", "validated"), flush=True)
            if any("report" in r for r in group["runs"]):
                group["status"] = "MEASURED"
        group["summary"] = summarize(group)
        save(data)
        print(name, group["status"])


def grade() -> None:
    data = json.loads(FILE.read_text(encoding="utf-8"))
    for name, group in data["providers"].items():
        for row in group["runs"]:
            if "report" not in row or row.get("manual_score") is not None:
                continue
            print(
                name,
                data["sites"][row["site"]]["text"],
                json.dumps(row["report"], indent=2),
                sep="\n",
            )
            checks = []
            for question in CHECKLIST:
                answer = ""
                while answer not in ("y", "n"):
                    answer = input(question + " [y/n] ").strip().lower()
                checks.append(answer == "y")
            row.update(checks=checks, manual_score=1 + sum(checks))
            group["summary"] = summarize(group)
            save(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--grade",
        action="store_true",
        help="Review saved reports without new API calls",
    )
    parser.add_argument("--runs", type=int, default=2)
    args = parser.parse_args()
    if args.grade:
        grade()
    elif args.runs < 2:
        parser.error("--runs must be at least two")
    else:
        asyncio.run(benchmark(args.runs))
