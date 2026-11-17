"""Load benchmark of the REST API: latency percentiles and throughput per endpoint and concurrency level.

    python scripts/benchmark_api.py --url http://localhost:8000 --requests 400 --concurrency 1 8 32

Writes docs/benchmark.md (and prints it). Run it against the Docker stack (production-like: 2 uvicorn workers) and
report the environment next to the numbers - latency figures without their context are meaningless.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import platform
import statistics
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]


def scenarios(station: str) -> dict[str, tuple[str, dict]]:
    return {
        "GET /health (liveness)": ("/health", {}),
        "GET /v1/priority?limit=50": ("/v1/priority", {"limit": 50}),
        "GET /v1/predict (1 station-hour)": ("/v1/predict", {"station_id": station, "hour": "2026-10-06T08:00:00",
                                                             "precipitation_mm": 2}),
        "GET /v1/rebalancing-plan (cached)": ("/v1/rebalancing-plan", {}),
    }


async def run(client: httpx.AsyncClient, path: str, params: dict, n: int, concurrency: int) -> dict:
    sem = asyncio.Semaphore(concurrency)
    lat, errors = [], 0

    async def one():
        nonlocal errors
        async with sem:
            t = time.perf_counter()
            try:
                r = await client.get(path, params=params)
                ok = r.status_code == 200
            except httpx.HTTPError:
                ok = False
            lat.append((time.perf_counter() - t) * 1000)
            errors += 0 if ok else 1

    start = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(n)))
    wall = time.perf_counter() - start
    q = statistics.quantiles(lat, n=100)
    return {"n": n, "c": concurrency, "p50": q[49], "p95": q[94], "p99": q[98], "rps": n / wall, "errors": errors}


async def main_async(args) -> str:
    headers = {"X-API-Key": os.environ["SMARTBIKE_API_KEY"]} if os.environ.get("SMARTBIKE_API_KEY") else {}
    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=60,
                                 limits=httpx.Limits(max_connections=max(args.concurrency))) as client:
        station = (await client.get("/v1/priority", params={"limit": 1})).json()[0]["station_id"]
        for path, params in scenarios(station).values():               # warm-up: caches, model load
            await client.get(path, params=params)
        lines = ["| Endpoint | Concurrence | p50 (ms) | p95 (ms) | p99 (ms) | Débit (req/s) | Erreurs |",
                 "|---|---:|---:|---:|---:|---:|---:|"]
        for label, (path, params) in scenarios(station).items():
            for c in args.concurrency:
                n = args.requests if "rebalancing" not in path else max(50, args.requests // 4)
                r = await run(client, path, params, n, c)
                lines.append(f"| {label} | {c} | {r['p50']:.1f} | {r['p95']:.1f} | {r['p99']:.1f} | "
                             f"{r['rps']:.0f} | {r['errors']} |")
                print(lines[-1], flush=True)
    env = (f"Cible : `{args.url}` · client : Python {platform.python_version()} / httpx, même machine "
           f"({platform.processor() or platform.machine()}, {os.cpu_count()} cœurs logiques). "
           f"{args.requests} requêtes par scénario (rebalancing : {max(50, args.requests // 4)}), après échauffement.")
    return "\n".join(["# Benchmark de l'API", "", env, "", *lines, ""])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--requests", type=int, default=400)
    ap.add_argument("--concurrency", type=int, nargs="+", default=[1, 8, 32])
    ap.add_argument("--out", default=str(ROOT / "docs" / "benchmark.md"))
    args = ap.parse_args()
    report = asyncio.run(main_async(args))
    pathlib.Path(args.out).write_text(report, encoding="utf-8")
    print(f"\nwritten {args.out}")


if __name__ == "__main__":
    main()
