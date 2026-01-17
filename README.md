# Execution Cost Estimator

Phase 1 of the execution cost estimator focuses on the core domain logic for computing trading costs using percent-of-ADV and square-root impact models. The repository currently ships the calculation logic, accompanying abstractions, and unit tests. Phase 2 creates the Postgres database, runs Alembic migrations in db/, and seeds initial data for symbols, liquidity, and impact models.

## Prerequisites

- Python 3.10+ 
- PostgreSQL 13+

## Setup

1. Create and activate a virtual environment (recommended):
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. Install the project in editable mode with the dev and db extras:
   ```bash
   pip install -e ".[test,worker,db,api]"
   ```

This installs the core dependency (`pydantic`) and the full test stack (pytest, pytest-asyncio, pytest-cov, etc.).

### 1 Start Postgres in Docker
```bash
# one-time named volume for persistence
docker volume create costdb_pg

# run Postgres 16 on localhost:5432 with default creds and DB name "costdb"
docker run -d --name costdb \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=costdb \
  -p 5432:5432 \
  -v costdb_pg:/var/lib/postgresql/data \
  postgres:16

# wait until ready
docker exec costdb pg_isready -U postgres
```

# Migrate schema using built-in defaults
   ```bash
   ce-db-up
   ```

# Seed initial data
   ```bash
   ce-db-seed
   ```

# To reset everything to defaults
   ```bash
   ce-db-reset
   ```

## Running the Unit Tests

Execute the calculator tests via:
```bash
python -m pytest
```

Integration tests:
```bash
pytest -q tests/integration
```

Pytest is configured to discover tests in the `tests/` directory, runs quietly by default, and now emits a terminal coverage report for the `cost_estimator` package on every run. To disable coverage, pass `--no-cov`. For an HTML report, add `--cov-report=html` and open `htmlcov/index.html` after the run completes.

## Security and Environment Guardrails

- API authentication: set `API_KEY` and include `X-API-Key: <value>` on all API routes except `/health`.
- Rate limiting: `RATE_LIMIT_PER_MIN` (default `60`) and `RATE_LIMIT_WINDOW_S` (default `60`) control the per-IP in-process limiter.
- Proxy awareness: set `TRUSTED_PROXY_IPS` (comma-separated IPs/CIDRs) to honor `X-Forwarded-For` for rate limiting when behind trusted proxies.
- HTTPS enforcement: set `ENFORCE_HTTPS=1` to redirect HTTP requests to HTTPS (honors `X-Forwarded-Proto`).
- Environment mode: `APP_ENV=dev|test|prod`. In `prod`, default DB/Redis URLs are rejected and TLS is required for Redis.
- Destructive DB reset: `ce-db-reset` requires `CE_DB_RESET_CONFIRM=1` and is blocked entirely when `APP_ENV=prod`.

## Price configuration overrides

The API infers a share price when requests omit explicit pricing details. The following environment variables control that lookup and therefore affect notional computation and the prices surfaced in API responses:

- `PRICE_<TICKER>_<DATE>` – highest-precedence override for a specific symbol and trade date. `<DATE>` must be ISO-8601 (`YYYY-MM-DD`).
- `PRICE_<TICKER>` – ticker-level fallback when a dated override is absent. The ticker is uppercased before lookup.
- `PRICE_TEST_DEFAULT` – convenience default for tests; also used in the worker’s square-root calculator to seed the ADV-derived price.
- `DEFAULT_SHARE_PRICE` – process-wide default when the ticker-specific variables are unset.

Values must parse as decimals (e.g., `12`, `12.34`, `1e2`). If none of the above variables are set or parseable, the system falls back to `$1.00` per share.

## Latency Benchmarks

The `benchmarks/` module provides lightweight latency checks for the core calculators. To exercise the defaults (100 warmup iterations, 1000 recorded runs per case), run:
```bash
python benchmarks/calculator_latency.py
```

You can tweak iterations with `--warmup` and `--runs` to explore different sampling depths.

## What’s Next

Future phases will add infrastructure adapters (Postgres, Redis, RQ) and FastAPI/RQ entrypoints. Those components will build on the core abstractions already present in `cost_estimator/core`.
