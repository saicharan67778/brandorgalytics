"""Save genuine request/response pairs from a running local API."""

import argparse
import json
from pathlib import Path

import httpx


def capture(base: str) -> None:
    folder = Path(__file__).resolve().parents[1] / "docs/samples"
    folder.mkdir(exist_ok=True, parents=True)
    cases = {
        "invalid-url": "not a url",
        "ssrf-block": "http://127.0.0.1/",
        "unreachable": "https://nonexistent.invalid/",
        "analysis": "https://plausible.io/",
    }
    with httpx.Client(base_url=base, timeout=150, trust_env=False) as client:
        health = client.get("/health")
        record = {
            "method": "GET",
            "path": "/health",
            "status": health.status_code,
            "response": health.json(),
        }
        (folder / "health.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
        for name, url in cases.items():
            payload = {"url": url}
            response = client.post("/link-analysis", json=payload)
            result = response.json()
            filename = (
                name
                if name != "analysis" or response.is_success
                else result["error"]["code"]
            )
            record = {
                "method": "POST",
                "path": "/link-analysis",
                "request": payload,
                "status": response.status_code,
                "response": result,
            }
            (folder / f"{filename}.json").write_text(
                json.dumps(record, indent=2), encoding="utf-8"
            )
            print(filename, response.status_code)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:4000")
    capture(parser.parse_args().base)
