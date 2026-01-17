# Execution Cost Estimator

Estimate trading costs via a FastAPI service backed by Postgres, Redis, and an RQ worker. The core models implement percent-of-ADV and square-root impact calculations and expose an API for ADV lookups and asynchronous cost estimation.

## Features

- FastAPI endpoints for ADV lookup and cost estimates.
- Postgres-backed persistence for requests, results, models, and liquidity.
- Redis-backed ADV cache.
- RQ queue and worker for async computation.
- CLI helpers for migrations and seeding.
- Security guardrails: API key auth, rate limiting, proxy awareness, HTTPS redirect.
- Benchmarks and tests.

## Architecture at a glance

- `cost_estimator/core`: domain models, calculators, and ports.
- `cost_estimator/adapters`: Postgres, Redis cache, RQ queue adapters.
- `cost_estimator/api`: FastAPI application.
- `cost_estimator/worker`: RQ job entrypoint.
- `cost_estimator/cli`: DB migration and seed commands.
- `db/`: Alembic config, migrations, seed data.

## Quickstart (local)

### Prerequisites

- Python 3.10+
- Postgres 13+
- Redis 6+

### 1) Create a virtual environment and install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[api,worker,db]"
```

### 2) Start Postgres and Redis (Docker)

```bash
# Postgres 16 on localhost:5432
docker volume create costdb_pg
docker run -d --name costdb \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=costdb \
  -p 5432:5432 \
  -v costdb_pg:/var/lib/postgresql/data \
  postgres:16

# Redis on localhost:6379
docker run -d --name costredis -p 6379:6379 redis:7
```

### 3) Configure environment

```bash
export APP_ENV=dev
export API_KEY=local-dev-key
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/costdb
export REDIS_URL=redis://localhost:6379/0
export DEFAULT_SHARE_PRICE=200
```

### 4) Apply migrations and seed data

```bash
ce-db-up
ce-db-seed
```

### 5) Start the worker

```bash
RQ_REDIS_URL=$REDIS_URL rq worker estimates
```

### 6) Start the API

```bash
uvicorn cost_estimator.api.main:app --reload
```

### 7) Try the API

```bash
# Health does not require auth
curl http://localhost:8000/health

# ADV lookup (seeded dates include 2025-09-17..19)
curl -H "X-API-Key: $API_KEY" \
  "http://localhost:8000/adv/AAPL?date=2025-09-19"

# Submit estimate
# Requires a price override (e.g., DEFAULT_SHARE_PRICE or PRICE_AAPL_2025-09-19)
curl -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"ticker":"AAPL","shares":1000,"side":"buy","date":"2025-09-19"}' \
  http://localhost:8000/estimate

# Fetch result using returned request_id
curl -H "X-API-Key: $API_KEY" \
  http://localhost:8000/estimate/<request_id>
```

## API endpoints

- `GET /health`: status check (no auth).
- `GET /adv/{ticker}?date=YYYY-MM-DD`: ADV lookup for a ticker/date.
- `POST /estimate`: submit an estimate request.
- `GET /estimate/{request_id}`: fetch status and results.

`POST /estimate` expects:

```json
{
  "ticker": "AAPL",
  "shares": 1000,
  "side": "buy",
  "date": "2025-09-19"
}
```

## Configuration

### Required

- `API_KEY`: required for all endpoints except `/health`.
- `DATABASE_URL` (or `DB_DSN` / `POSTGRES_DSN`): Postgres connection string.

### Common optional

- `APP_ENV`: `dev`, `test`, or `prod` (default `dev`).
- `REDIS_URL`: Redis connection string (defaults to `redis://localhost:6379/0` outside prod).
- `RATE_LIMIT_PER_MIN`: requests per minute per client IP (default `60`).
- `RATE_LIMIT_WINDOW_S`: rate-limit window in seconds (default `60`).
- `TRUSTED_PROXY_IPS`: comma-separated IPs/CIDRs to trust for `X-Forwarded-For`.
- `ENFORCE_HTTPS`: set to `1`/`true` to redirect HTTP to HTTPS.

### RQ queue settings

- `RQ_REDIS_URL`: Redis URL for the queue (falls back to `REDIS_URL`).
- `RQ_QUEUE_NAME`: queue name (default `estimates`).
- `RQ_JOB_FUNC`: dotted path for the worker function (default `cost_estimator.worker.worker.compute_cost`).
- `RQ_JOB_TIMEOUT`: job timeout in seconds (default `120`).
- `RQ_RESULT_TTL`: job result TTL (default `0`).
- `RQ_FAILURE_TTL`: failed job TTL in seconds (default `86400`).
- `RQ_RETRY_MAX`: retry attempts (default `3`).
- `RQ_RETRY_INTERVALS`: comma-separated retry intervals (default `10,30,90`).

### Price lookup overrides (required for /estimate)

At least one price override must be set for the requested ticker/date or the API
will return 400 to avoid misleading notional calculations.

- `PRICE_<TICKER>_<DATE>`: per-ticker, per-date override (`YYYY-MM-DD`).
- `PRICE_<TICKER>`: per-ticker override.
- `PRICE_TEST_DEFAULT`: test helper override.
- `DEFAULT_SHARE_PRICE`: process-wide override.

### Impact model parameter requirements

Cost estimation fails fast if required parameters are missing:

- `pct_adv` requires `c` (cap is optional).
- `sqrt` requires `A` and `B`.

### Production guardrails

- `APP_ENV=prod` requires explicit `DATABASE_URL` and Redis URLs.
- `REDIS_URL` / `RQ_REDIS_URL` must use `rediss://` in prod.
- `ce-db-reset` is blocked when `APP_ENV=prod` and requires `CE_DB_RESET_CONFIRM=1` otherwise.

## Database operations

```bash
# Apply Alembic migrations
ce-db-up

# Seed reference data (symbols, liquidity, impact models)
ce-db-seed

# Reset DB to defaults (blocked in prod)
CE_DB_RESET_CONFIRM=1 ce-db-reset
```

## Running tests

```bash
ruff check .
ruff format --check .
python -m pytest
pytest -q tests/integration
```

Integration tests require Docker for Postgres and Redis via testcontainers.

## Benchmarks

```bash
python benchmarks/calculator_latency.py
```

## Contributing

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,test,api,worker,db]"
```

### Guidelines

- Keep domain logic in `cost_estimator/core` and expose infrastructure through ports/adapters.
- Use `Decimal` for price and cost math in core logic.
- Keep API, worker, and CLI changes small and well-tested.
- Prefer adding focused tests alongside changes in `tests/`.

### Useful commands

```bash
ruff check .
ruff format .
python -m pytest
pytest -q tests/integration
```
