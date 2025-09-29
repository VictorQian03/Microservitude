# Execution Cost Estimator

Phase 1 of the execution cost estimator focuses on the core domain logic for computing trading costs using percent-of-ADV and square-root impact models. The repository currently ships the calculation logic, accompanying abstractions, and unit tests.

## Prerequisites

- Python 3.10+
- (Optional) A virtual environment tool such as `python -m venv`

## Setup

1. Create and activate a virtual environment (recommended):
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. Install the project in editable mode with the dev extras:
   ```bash
   pip install -e '.[dev]'
   ```

This installs the core dependency (`pydantic`) and the development dependency (`pytest`).

## Running the Unit Tests

Execute the calculator tests via:
```bash
python -m pytest
```

Pytest is configured to discover tests in the `tests/` directory and runs quietly by default.

## Latency Benchmarks

The `benchmarks/` module provides lightweight latency checks for the core calculators. To exercise the defaults (100 warmup iterations, 1000 recorded runs per case), run:
```bash
python benchmarks/calculator_latency.py
```

You can tweak iterations with `--warmup` and `--runs` to explore different sampling depths.

## What’s Next

Future phases will add infrastructure adapters (Postgres, Redis, RQ) and FastAPI/RQ entrypoints. Those components will build on the core abstractions already present in `cost_estimator/core`.
