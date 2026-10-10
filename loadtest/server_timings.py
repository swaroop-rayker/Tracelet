"""Server-side request timings from the api's own log (ADR-0027, NFR2.AC1, NFR2.AC4).

    docker compose logs --since 30m api | python3 loadtest/server_timings.py

Reads the `http_request` events (structlog JSON, one per request, with `duration_ms`) and
reports percentiles per class: capture (`/r/...`, NFR2.AC1: p95 under 50 ms of server
processing), the dashboard API (`/api/v1/...` except the probes and the capture page's
own calls, NFR2.AC4: p95 under 300 ms), and the rest. Standard library only: it runs on
the VM's own Python.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

ENRICH_PREFIXES = ("/api/v1/s/", "/api/v1/hp/")


def kind(path: str) -> str:
    if path.startswith("/r/") or path in ("/r", "/r/"):
        return "capture"
    if path.startswith(ENRICH_PREFIXES):
        return "enrichment"
    if path.startswith("/api/v1/"):
        return "dashboard api"
    return "other"


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))]


def main() -> int:
    timings: dict[str, list[float]] = defaultdict(list)
    statuses: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for raw in sys.stdin:
        start = raw.find("{")
        if start < 0:
            continue
        try:
            event = json.loads(raw[start:])
        except ValueError:
            continue
        if event.get("event") != "http_request" or "duration_ms" not in event:
            continue
        k = kind(str(event.get("path", "")))
        timings[k].append(float(event["duration_ms"]))
        statuses[k][int(event.get("status", 0))] += 1
    for k in ("capture", "dashboard api", "enrichment", "other"):
        values = timings.get(k)
        if not values:
            continue
        print(
            f"{k:14} n={len(values):6}  p50 {pct(values, 50):7.1f}  p95 {pct(values, 95):7.1f}"
            f"  p99 {pct(values, 99):7.1f}  max {max(values):8.1f} ms  status {dict(statuses[k])}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
