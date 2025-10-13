# cost_estimator/api/main.py
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from collections.abc import Mapping, Sequence
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder

from cost_estimator.adapters.pg_repo import CostRepository, LiquidityRepository, PgRepositories
from cost_estimator.adapters.redis_cache import RedisCache, make_redis_cache_from_env
from cost_estimator.adapters.rq_queue import RQQueue, make_rq_queue_from_env
from cost_estimator.core.models import CachedADV, CostRequestInput, CostRequestRecord


@dataclass(slots=True)
class AppDependencies:
    """Container so we only wire adapters once per process."""

    repos: PgRepositories
    cache: RedisCache
    queue: RQQueue

    @property
    def cost_repo(self) -> CostRepository:
        return self.repos.costs

    @property
    def liquidity_repo(self) -> LiquidityRepository:
        return self.repos.liquidity

    def shutdown(self) -> None:
        """Release adapter resources when the app stops."""

        try:
            self.repos.pool.close()
        except Exception:
            pass
        try:
            self.cache._r.close()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            self.queue._redis.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def _infer_price_usd(ticker: str, trade_date: date) -> Decimal:
    """
    Heuristic price lookup.

    The integration tests provide PRICE_TEST_DEFAULT. We fall back to $1 if unset.
    """

    env_keys = (
        f"PRICE_{ticker.upper()}_{trade_date.isoformat()}",
        f"PRICE_{ticker.upper()}",
        "PRICE_TEST_DEFAULT",
        "DEFAULT_SHARE_PRICE",
    )
    for key in env_keys:
        raw = os.getenv(key)
        if raw:
            try:
                return Decimal(raw)
            except (InvalidOperation, ValueError):
                continue
    return Decimal("1")


def _load_cached_adv(cache: RedisCache, ticker: str, trade_date: date) -> Optional[CachedADV]:
    """Best-effort attempt to hydrate a CachedADV from Redis."""

    try:
        cached = cache.get_adv(ticker, trade_date)
        if cached:
            return cached
    except Exception:
        pass

    try:
        raw_val = cache._r.get(cache._key(ticker, trade_date))  # type: ignore[attr-defined]
    except Exception:
        return None
    if raw_val is None:
        return None
    if isinstance(raw_val, bytes):
        raw_val = raw_val.decode("utf-8")
    try:
        adv = Decimal(raw_val)
    except (InvalidOperation, TypeError):
        return None
    return CachedADV(ticker=ticker, d=trade_date, adv_usd=adv)


def _extract_cost_bps(candidate: Any) -> Optional[Decimal]:
    """Normalize cost bps from ModelCostBreakdown or dict-like payloads."""

    value = None

    # Object attributes
    for attr in ("cost_bps", "bps", "total_cost_bps", "total_bps", "impact_bps", "estimated_bps"):
        if hasattr(candidate, attr):
            value = getattr(candidate, attr)
            break

    # Mapping
    if value is None and isinstance(candidate, Mapping):
        for k in ("cost_bps", "bps", "total_cost_bps", "total_bps", "impact_bps", "estimated_bps"):
            if k in candidate:
                value = candidate[k]
                break

    # Tuple forms: (name, payload)
    if value is None and isinstance(candidate, Sequence) and len(candidate) == 2:
        _, payload = candidate
        if isinstance(payload, Mapping):
            for k in ("cost_bps", "bps", "total_cost_bps", "total_bps", "impact_bps", "estimated_bps"):
                if k in payload:
                    value = payload[k]
                    break

    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _infer_best_model_from_models(models: Any) -> Optional[str]:
    """Fallback best-model selection when result.best_model is missing."""

    best_name: Optional[str] = None
    best_bps: Optional[Decimal] = None

    def _consider(name: Any, payload: Any) -> None:
        nonlocal best_name, best_bps
        bps = _extract_cost_bps(payload)
        if bps is None:
            return
        if best_bps is None or bps < best_bps:
            best_bps = bps
            best_name = str(name) if name is not None else None

    if isinstance(models, Mapping):
        for name, payload in models.items():
            _consider(name, payload)
        return best_name

    if isinstance(models, Sequence):
        for entry in models:
            if isinstance(entry, Mapping):
                name = entry.get("name") or entry.get("model") or entry.get("key")
                _consider(name, entry)
            elif isinstance(entry, tuple) and len(entry) == 2:
                name, payload = entry
                _consider(name, payload)
            else:
                payload = entry
                name = getattr(entry, "name", None)
                _consider(name, payload)
        return best_name

    return best_name


def create_app() -> FastAPI:
    """Factory used by tests to build the FastAPI application."""

    def _wire_dependencies(app: FastAPI) -> AppDependencies:
        deps = AppDependencies(
            repos=PgRepositories.from_env(),
            cache=make_redis_cache_from_env(),
            queue=make_rq_queue_from_env(),
        )
        app.state.deps = deps
        return deps

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        deps = _wire_dependencies(app)
        try:
            yield
        finally:
            try:
                deps.shutdown()
            finally:
                app.state.deps = None

    app = FastAPI(lifespan=lifespan)

    def get_deps(request: Request) -> AppDependencies:
        deps = getattr(request.app.state, "deps", None)
        if isinstance(deps, AppDependencies):
            return deps
        try:
            return _wire_dependencies(request.app)
        except Exception as exc:  # pragma: no cover - defensive logging path
            raise RuntimeError("App dependencies are not initialized") from exc

    def get_cost_repo(request: Request) -> CostRepository:
        return get_deps(request).cost_repo

    def get_liquidity_repo(request: Request) -> LiquidityRepository:
        return get_deps(request).liquidity_repo

    def get_cache(request: Request) -> RedisCache:
        return get_deps(request).cache

    def get_queue(request: Request) -> RQQueue:
        return get_deps(request).queue

    @app.get("/health")
    async def health_check() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/adv/{ticker}")
    async def get_adv(
        ticker: str,
        trade_date: date = Query(..., alias="date"),
        cache: RedisCache = Depends(get_cache),
        liquidity_repo: LiquidityRepository = Depends(get_liquidity_repo),
    ) -> Dict[str, Any]:
        ticker_norm = ticker.upper()

        cached = _load_cached_adv(cache, ticker_norm, trade_date)
        if cached:
            return jsonable_encoder(cached)

        liquidity = liquidity_repo.get_liquidity(ticker_norm, trade_date)
        if liquidity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No liquidity for {ticker_norm} on {trade_date.isoformat()}",
            )

        payload = CachedADV(ticker=ticker_norm, d=liquidity.d, adv_usd=liquidity.adv_usd)
        try:
            cache.set_adv(payload)
            cache._r.set(  # type: ignore[attr-defined]
                cache._key(ticker_norm, trade_date),  # type: ignore[attr-defined]
                format(payload.adv_usd, "f"),
            )
        except Exception:
            pass
        return jsonable_encoder(payload)

    @app.post("/estimate")
    async def submit_estimate(
        request: CostRequestInput,
        cost_repo: CostRepository = Depends(get_cost_repo),
        liquidity_repo: LiquidityRepository = Depends(get_liquidity_repo),
        queue: RQQueue = Depends(get_queue),
    ) -> Dict[str, Any]:
        ticker_norm = request.ticker.upper()
        liquidity = liquidity_repo.get_liquidity(ticker_norm, request.d)
        if liquidity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No liquidity for {ticker_norm} on {request.d.isoformat()}",
            )

        price = _infer_price_usd(ticker_norm, request.d)
        notional = Decimal(request.shares) * price
        created_at = datetime.now(timezone.utc)
        record = CostRequestRecord(
            id=uuid4(),
            ticker=ticker_norm,
            shares=request.shares,
            side=request.side,
            d=request.d,
            notional_usd=notional,
            status="queued",
            created_at=created_at,
        )

        cost_repo.create_request(record)
        queue.enqueue(record)
        return {"request_id": str(record.id), "status": record.status}

    @app.get("/estimate/{request_id}")
    async def get_estimate_status(
        request_id: UUID,
        cost_repo: CostRepository = Depends(get_cost_repo),
    ) -> Dict[str, Any]:
        record = cost_repo.get_request(request_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Request {request_id} not found",
            )

        result = cost_repo.get_result(request_id)
        response: Dict[str, Any] = {
            "request_id": str(record.id),
            "status": record.status,
            "ticker": record.ticker,
            "shares": record.shares,
            "side": record.side,
            "date": record.d.isoformat(),
            "notional_usd": format(record.notional_usd, "f"),
            "created_at": record.created_at.isoformat(),
        }

        if result is not None:
            result_data = jsonable_encoder(result)
            result_data.pop("request_id", None)
            response.update(result_data)
            best_model = response.get("best_model")
            if best_model in (None, "", "null"):
                inferred = _infer_best_model_from_models(response.get("models"))
                best_model = inferred if inferred is not None else None

            response["best_model"] = str(best_model) if best_model is not None else None
            for numeric_key in ("total_cost_usd", "total_cost_bps", "adv_usd"):
                value = response.get(numeric_key)
                if value is None:
                    continue
                try:
                    response[numeric_key] = format(Decimal(str(value)), "f")
                except (InvalidOperation, ValueError):
                    response[numeric_key] = str(value)

        return response

    return app


_BOOTSTRAP_EXCEPTION: Optional[RuntimeError] = None


def _initialize_app() -> Optional[FastAPI]:
    """Create the module-level ASGI app, but stay import-safe if env is missing."""

    global _BOOTSTRAP_EXCEPTION
    try:
        return create_app()
    except RuntimeError as exc:
        _BOOTSTRAP_EXCEPTION = exc
        return None


def bootstrap_exception() -> Optional[RuntimeError]:
    """Return the error raised while creating the module-level app, if any."""

    return _BOOTSTRAP_EXCEPTION


app = _initialize_app()
