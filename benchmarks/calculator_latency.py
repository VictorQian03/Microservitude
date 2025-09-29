"""Latency benchmarks for core calculator functions."""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Callable, Iterable, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cost_estimator.core.calculators import calculate_pct_adv_cost, calculate_sqrt_cost


@dataclass
class BenchmarkResult:
    name: str
    runs: int
    total_seconds: float
    avg_microseconds: float
    min_microseconds: float
    max_microseconds: float


@dataclass
class BenchmarkCase:
    name: str
    description: str
    callable: Callable[[], None]


def measure(case: BenchmarkCase, runs: int, warmup: int) -> BenchmarkResult:
    """Measure latency for a benchmark case."""

    for _ in range(warmup):
        case.callable()

    samples: List[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        case.callable()
        samples.append(time.perf_counter() - start)

    total = sum(samples)
    avg = statistics.mean(samples)

    return BenchmarkResult(
        name=case.name,
        runs=runs,
        total_seconds=total,
        avg_microseconds=avg * 1e6,
        min_microseconds=min(samples) * 1e6,
        max_microseconds=max(samples) * 1e6,
    )


def get_cases() -> Iterable[BenchmarkCase]:
    return [
        BenchmarkCase(
            name="pct_adv_basic",
            description="pct_adv: 1MM notional vs 20MM ADV, cap 10%",
            callable=partial(
                calculate_pct_adv_cost,
                notional_usd=Decimal("1000000"),
                adv_usd=Decimal("20000000"),
                c=Decimal("0.5"),
                cap=Decimal("0.1"),
            ),
        ),
        BenchmarkCase(
            name="pct_adv_high_participation",
            description="pct_adv: 10MM notional vs 15MM ADV, cap 50%",
            callable=partial(
                calculate_pct_adv_cost,
                notional_usd=Decimal("10000000"),
                adv_usd=Decimal("15000000"),
                c=Decimal("0.4"),
                cap=Decimal("0.5"),
            ),
        ),
        BenchmarkCase(
            name="sqrt_basic",
            description="sqrt: 100k shares vs 1MM ADV shares",
            callable=partial(
                calculate_sqrt_cost,
                shares=100000,
                adv_shares=Decimal("1000000"),
                price=Decimal("10"),
                a=Decimal("50"),
                b=Decimal("10"),
            ),
        ),
        BenchmarkCase(
            name="sqrt_large_order",
            description="sqrt: 5MM shares vs 2MM ADV shares",
            callable=partial(
                calculate_sqrt_cost,
                shares=5000000,
                adv_shares=Decimal("2000000"),
                price=Decimal("25"),
                a=Decimal("75"),
                b=Decimal("15"),
            ),
        ),
    ]


def format_result(result: BenchmarkResult) -> str:
    return (
        f"{result.name:24} | runs: {result.runs:6d} | "
        f"avg: {result.avg_microseconds:10.2f} µs | "
        f"min: {result.min_microseconds:10.2f} µs | "
        f"max: {result.max_microseconds:10.2f} µs | "
        f"total: {result.total_seconds:.4f} s"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Latency benchmarks for calculator functions.")
    parser.add_argument("--runs", type=int, default=1000, help="Number of recorded runs per case")
    parser.add_argument("--warmup", type=int, default=100, help="Number of warmup iterations per case")
    args = parser.parse_args()

    cases = list(get_cases())
    results = [measure(case, runs=args.runs, warmup=args.warmup) for case in cases]

    print("Calculator Latency Benchmarks")
    print("-" * 80)
    for case in cases:
        print(f"{case.name:24} :: {case.description}")
    print("-" * 80)

    for result in results:
        print(format_result(result))


if __name__ == "__main__":
    main()
