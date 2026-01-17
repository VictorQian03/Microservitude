from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, Optional, Tuple
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from cost_estimator.api import main as api_main
from cost_estimator.core.models import (
    CachedADV,
    CostRequestRecord,
    CostResult,
    Liquidity,
    ModelCostBreakdown,
)


def _as_uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


class _DummyCloser:
    def close(self) -> None:
        pass


class FakeCostRepo:
    def __init__(self) -> None:
        self.requests: Dict[UUID, CostRequestRecord] = {}
        self.results: Dict[UUID, CostResult] = {}

    def create_request(self, request: CostRequestRecord) -> None:
        self.requests[request.id] = request

    def update_status(self, request_id: UUID | str, status: str) -> None:
        rid = _as_uuid(request_id)
        record = self.requests.get(rid)
        if record:
            record.status = status
            self.requests[rid] = record

    def get_request(self, request_id: UUID | str) -> Optional[CostRequestRecord]:
        rid = _as_uuid(request_id)
        return self.requests.get(rid)

    def save_result(self, result: CostResult | None = None, **kwargs) -> None:
        if result is None:
            raise AssertionError("Result payload is required for FakeCostRepo")
        self.results[result.request_id] = result

    def get_result(self, request_id: UUID | str) -> Optional[CostResult]:
        rid = _as_uuid(request_id)
        return self.results.get(rid)


class FakeLiquidityRepo:
    def __init__(self, liquidity_map: Dict[Tuple[str, date], Decimal]) -> None:
        self._data = liquidity_map

    def get_liquidity(self, ticker: str, d: date) -> Optional[Liquidity]:
        key = (ticker.upper(), d)
        adv = self._data.get(key)
        if adv is None:
            return None
        return Liquidity(ticker=key[0], d=d, adv_usd=adv)


class FakeCache:
    def __init__(self) -> None:
        self._cache: Dict[str, CachedADV] = {}
        self._raw: Dict[str, str] = {}
        self._r = self  # Satisfy shutdown() expecting .close()/set()/get()

    def _key(self, ticker: str, d: date) -> str:
        return f"adv:{ticker.upper()}:{d.isoformat()}"

    def get(self, key: str) -> Optional[str]:
        return self._raw.get(key)

    def set(self, key: str, value: str) -> None:
        self._raw[key] = value

    def close(self) -> None:
        pass

    def get_adv(self, ticker: str, d: date) -> Optional[CachedADV]:
        return self._cache.get(self._key(ticker, d))

    def set_adv(self, payload: CachedADV, ttl_seconds: int | None = None) -> None:  # noqa: ARG002 - parity with real adapter
        key = self._key(payload.ticker, payload.d)
        self._cache[key] = payload
        self._raw[key] = str(payload.adv_usd)


class FakeQueue:
    def __init__(self, cost_repo: FakeCostRepo) -> None:
        self.cost_repo = cost_repo
        self.pending: list[UUID] = []
        self._redis = _DummyCloser()

    def enqueue(self, request: CostRequestRecord) -> str:
        rid = request.id
        self.pending.append(rid)
        return str(rid)

    def process_all(self) -> None:
        now = datetime.now(timezone.utc)
        for rid in list(self.pending):
            request = self.cost_repo.get_request(rid)
            if not request:
                continue
            result = CostResult(
                request_id=request.id,
                adv_usd=Decimal("5000000000"),
                models={
                    "pct_adv": ModelCostBreakdown(
                        name="pct_adv",
                        version=1,
                        parameters={"c": Decimal("0.5")},
                        cost_usd=Decimal("4000"),
                        cost_bps=Decimal("20"),
                    )
                },
                best_model="pct_adv",
                total_cost_usd=Decimal("4000"),
                total_cost_bps=Decimal("20"),
                computed_at=now,
            )
            self.cost_repo.save_result(result)
            self.cost_repo.update_status(request.id, "done")
        self.pending.clear()


class FakePgRepos:
    def __init__(self, liquidity_repo: FakeLiquidityRepo, cost_repo: FakeCostRepo) -> None:
        self.liquidity = liquidity_repo
        self.costs = cost_repo
        self.pool = _DummyCloser()

    @property
    def models(self) -> None:
        return None


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rate_limit_per_min: str | None = None,
    set_price_env: bool = True,
) -> Tuple[TestClient, FakeQueue, FakeCache]:
    cost_repo = FakeCostRepo()
    liquidity_repo = FakeLiquidityRepo({("AAPL", date(2025, 9, 19)): Decimal("5000000000")})
    cache = FakeCache()
    queue = FakeQueue(cost_repo)
    repos = FakePgRepos(liquidity_repo, cost_repo)

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("API_KEY", "test-api-key")
    if set_price_env:
        monkeypatch.setenv("PRICE_TEST_DEFAULT", "200")
    else:
        for key in (
            "PRICE_TEST_DEFAULT",
            "DEFAULT_SHARE_PRICE",
            "PRICE_AAPL",
            "PRICE_AAPL_2025-09-19",
        ):
            monkeypatch.delenv(key, raising=False)
    if rate_limit_per_min is not None:
        monkeypatch.setenv("RATE_LIMIT_PER_MIN", rate_limit_per_min)
    monkeypatch.setattr(
        api_main.PgRepositories,
        "from_env",
        classmethod(lambda cls, env_var="DATABASE_URL": repos),
    )
    monkeypatch.setattr(
        api_main, "make_redis_cache_from_env", lambda env_var="REDIS_URL", namespace="adv": cache
    )
    monkeypatch.setattr(api_main, "make_rq_queue_from_env", lambda: queue)

    app = api_main.create_app()
    client = TestClient(app)
    return client, queue, cache


@pytest.fixture()
def fastapi_client(monkeypatch: pytest.MonkeyPatch) -> Tuple[TestClient, FakeQueue, FakeCache]:
    client, queue, cache = _build_client(monkeypatch)
    try:
        yield client, queue, cache
    finally:
        client.close()


def test_estimate_request_lifecycle(
    fastapi_client: Tuple[TestClient, FakeQueue, FakeCache],
) -> None:
    client, queue, _cache = fastapi_client
    headers = {"X-API-Key": "test-api-key"}

    body = {"ticker": "AAPL", "shares": 1000, "side": "buy", "date": "2025-09-19"}
    resp = client.post("/estimate", json=body, headers=headers)
    assert resp.status_code == 200
    payload = resp.json()
    rid = payload["request_id"]
    assert payload["status"] == "queued"

    status_resp = client.get(f"/estimate/{rid}", headers=headers)
    assert status_resp.status_code == 200
    status_payload = status_resp.json()
    assert status_payload["status"] == "queued"
    assert status_payload["ticker"] == "AAPL"
    assert Decimal(status_payload["notional_usd"]) == Decimal("200000")

    queue.process_all()

    final_resp = client.get(f"/estimate/{rid}", headers=headers)
    assert final_resp.status_code == 200
    final_payload = final_resp.json()
    assert final_payload["status"] == "done"
    assert final_payload["best_model"] == "pct_adv"
    assert Decimal(final_payload["total_cost_usd"]) == Decimal("4000")
    assert Decimal(final_payload["total_cost_bps"]) == Decimal("20")


def test_health_is_public(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _queue, _cache = _build_client(monkeypatch)
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
    finally:
        client.close()


def test_missing_api_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _queue, _cache = _build_client(monkeypatch)
    try:
        body = {"ticker": "AAPL", "shares": 1000, "side": "buy", "date": "2025-09-19"}
        resp = client.post("/estimate", json=body)
        assert resp.status_code == 401
    finally:
        client.close()


def test_rate_limit_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _queue, _cache = _build_client(monkeypatch, rate_limit_per_min="1")
    headers = {"X-API-Key": "test-api-key"}
    try:
        resp1 = client.get("/adv/AAPL", params={"date": "2025-09-19"}, headers=headers)
        assert resp1.status_code == 200
        resp2 = client.get("/adv/AAPL", params={"date": "2025-09-19"}, headers=headers)
        assert resp2.status_code == 429
    finally:
        client.close()


def test_missing_price_override_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _queue, _cache = _build_client(monkeypatch, set_price_env=False)
    headers = {"X-API-Key": "test-api-key"}
    try:
        body = {"ticker": "AAPL", "shares": 1000, "side": "buy", "date": "2025-09-19"}
        resp = client.post("/estimate", json=body, headers=headers)
        assert resp.status_code == 400
        payload = resp.json()
        assert "price override" in payload["detail"].lower()
    finally:
        client.close()
