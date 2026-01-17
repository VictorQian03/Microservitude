import json
import os
import re
import time
import uuid
from contextlib import closing
from datetime import date
from math import sqrt

import psycopg
import pytest
from redis import Redis
from rq import Queue, SimpleWorker
from rq.serializers import JSONSerializer
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

# ---------- constants ----------
TEST_TICKER = "AAPL"
TEST_DATE = date(2025, 9, 19)
# Choose price and ADV$ so formulas are clean.
TEST_PRICE = 200.0
TEST_ADV_USD = 5_000_000_000.0  # $5B ADV
TEST_SHARES = 100_000
TEST_SIDE = "buy"
# Derived
TEST_NOTIONAL = TEST_SHARES * TEST_PRICE
ADV_SHARES = TEST_ADV_USD / TEST_PRICE  # 25,000,000 shares

# pct_adv: q = min(Q*P/ADV$, cap) = min(0.004, 0.1) = 0.004
# cost_usd = c*q*Q*P. Choose c=0.5 -> 20 bps of notional = $40,000
PCT_ADV_PARAMS = {"c": 0.5, "cap": 0.1}
PCT_ADV_EXPECT_BPS = 20.0
PCT_ADV_EXPECT_USD = TEST_NOTIONAL * PCT_ADV_EXPECT_BPS / 1e4

# sqrt: cost_bps = A*sqrt(Q/ADV_shares) + B. Choose A=300, B=0.
# sqrt(0.004) ~= 0.0632455532. bps ~= 18.9737
SQRT_PARAMS = {"A": 300.0, "B": 0.0}

SQRT_EXPECT_BPS = SQRT_PARAMS["A"] * sqrt(TEST_SHARES / ADV_SHARES) + SQRT_PARAMS["B"]
SQRT_EXPECT_USD = TEST_NOTIONAL * SQRT_EXPECT_BPS / 1e4

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS symbols(
  id SERIAL PRIMARY KEY,
  ticker TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_liquidity(
  ticker TEXT REFERENCES symbols(ticker),
  d DATE,
  adv_usd NUMERIC NOT NULL CHECK (adv_usd > 0),
  PRIMARY KEY (ticker, d)
);
CREATE TABLE IF NOT EXISTS impact_models(
  name TEXT,
  version INT,
  params JSONB,
  active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT now(),
  PRIMARY KEY(name, version)
);
CREATE TABLE IF NOT EXISTS cost_requests(
  id UUID PRIMARY KEY,
  ticker TEXT NOT NULL REFERENCES symbols(ticker),
  shares BIGINT NOT NULL,
  side TEXT CHECK (side IN ('buy','sell')) NOT NULL,
  d DATE NOT NULL,
  notional_usd NUMERIC NOT NULL CHECK (notional_usd > 0),
  status TEXT CHECK (status IN ('queued','done','error')) NOT NULL,
  created_at TIMESTAMP DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cost_results(
  request_id UUID REFERENCES cost_requests(id) PRIMARY KEY,
  adv_usd NUMERIC,
  models JSONB,
  best_model TEXT,
  total_cost_usd NUMERIC NOT NULL,
  total_cost_bps NUMERIC NOT NULL,
  computed_at TIMESTAMP DEFAULT now()
);
"""

SEED_SQL = """
INSERT INTO symbols(ticker) VALUES (%(ticker)s)
ON CONFLICT (ticker) DO NOTHING;

INSERT INTO daily_liquidity(ticker, d, adv_usd)
VALUES (%(ticker)s, %(d)s, %(adv_usd)s)
ON CONFLICT (ticker, d) DO UPDATE SET adv_usd = EXCLUDED.adv_usd;

INSERT INTO impact_models(name, version, params, active)
VALUES ('pct_adv', 1, %(pct_params)s::jsonb, TRUE)
ON CONFLICT (name, version) DO UPDATE SET params = EXCLUDED.params, active = TRUE;

INSERT INTO impact_models(name, version, params, active)
VALUES ('sqrt', 1, %(sqrt_params)s::jsonb, TRUE)
ON CONFLICT (name, version) DO UPDATE SET params = EXCLUDED.params, active = TRUE;
"""


def _exec_many(conn, sql: str, params=None):
    """
    psycopg3 forbids multiple commands in prepared statements.
    Run statements one by one using simple query mode (prepare=False).
    """
    stmts = [s.strip() for s in sql.split(";") if s.strip()]
    with conn.cursor() as cur:
        for stmt in stmts:
            if params:
                cur.execute(stmt, params, prepare=False)
            else:
                cur.execute(stmt, prepare=False)
    conn.commit()


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16") as pg:
        pg.start()
        yield pg


@pytest.fixture(scope="session")
def redis_container():
    with RedisContainer("redis:7") as rc:
        rc.start()
        yield rc


@pytest.fixture(scope="session")
def db_dsn(postgres_container):
    url = postgres_container.get_connection_url()
    return re.sub(r"^\w+\+psycopg2://", "postgresql://", url)


@pytest.fixture(scope="session")
def redis_url(redis_container):
    return f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}"


@pytest.fixture(scope="session")
def db_conn(db_dsn):
    with closing(psycopg.connect(db_dsn, autocommit=False)) as conn:
        _exec_many(conn, SCHEMA_SQL)
        _exec_many(
            conn,
            SEED_SQL,
            {
                "ticker": TEST_TICKER,
                "d": TEST_DATE,
                "adv_usd": TEST_ADV_USD,
                "pct_params": json.dumps(PCT_ADV_PARAMS),
                "sqrt_params": json.dumps(SQRT_PARAMS | {"price_hint": TEST_PRICE}),
            },
        )
        yield conn


@pytest.fixture(scope="session")
def redis_client(redis_url):
    r = Redis.from_url(redis_url, decode_responses=False)
    r.flushdb()
    yield r
    r.flushdb()


@pytest.fixture(scope="session")
def env_wiring(db_dsn, redis_url):
    os.environ["APP_ENV"] = "test"
    os.environ["DATABASE_URL"] = db_dsn
    os.environ["DB_DSN"] = db_dsn
    os.environ["POSTGRES_DSN"] = db_dsn
    os.environ["REDIS_URL"] = redis_url
    os.environ["RQ_REDIS_URL"] = redis_url
    os.environ["RQ_QUEUE_NAME"] = "estimates"
    os.environ["API_KEY"] = "integration-test-key"
    # Price hint used by worker if implemented
    os.environ.setdefault("PRICE_TEST_DEFAULT", str(TEST_PRICE))
    yield


@pytest.fixture(scope="session")
def app(env_wiring):
    # Import FastAPI app.
    mod = pytest.importorskip("cost_estimator.api.main")
    return getattr(mod, "app", None) or getattr(mod, "create_app")()


@pytest.fixture
async def http_client(app):
    import httpx
    from asgi_lifespan import LifespanManager

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": os.environ.get("API_KEY", "")},
        ) as client:
            yield client


@pytest.fixture
def rq_queue(redis_client):
    return Queue("estimates", connection=redis_client, serializer=JSONSerializer)


@pytest.fixture
def rq_worker(redis_client, rq_queue):
    worker = SimpleWorker([rq_queue], connection=redis_client, serializer=JSONSerializer)
    return worker


# Utility to insert a queued request row directly if needed
def insert_request(
    conn,
    *,
    ticker=TEST_TICKER,
    d=TEST_DATE,
    shares=TEST_SHARES,
    side=TEST_SIDE,
    notional=TEST_NOTIONAL,
):
    rid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cost_requests(id, ticker, shares, side, d, notional_usd, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'queued')
            """,
            (str(rid), ticker, shares, side, d, notional),
        )
    conn.commit()
    return str(rid)


def fetch_result(conn, rid):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT best_model, total_cost_bps, total_cost_usd, models::text FROM cost_results WHERE request_id = %s",
            (rid,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "best_model": row[0],
            "bps": float(row[1]),
            "usd": float(row[2]),
            "models": json.loads(row[3]),
        }


def wait_until(fn, timeout=5.0, interval=0.05):
    start = time.time()
    while time.time() - start < timeout:
        out = fn()
        if out:
            return out
        time.sleep(interval)
    return None
