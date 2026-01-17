# cost_estimator/api/main.py
from __future__ import annotations

import hmac
import os
import threading
import time
from collections import deque
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import RedirectResponse

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


class _RateLimiter:
    def __init__(self, *, limit: int, window_s: int) -> None:
        self._limit = limit
        self._window_s = window_s
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - self._window_s
        with self._lock:
            bucket = self._hits.get(key)
            if bucket is None:
                bucket = deque()
                self._hits[key] = bucket
            while bucket and bucket[0] <= window_start:
                bucket.popleft()
            if len(bucket) >= self._limit:
                return False
            bucket.append(now)
        return True


def _trusted_proxy_networks() -> list[IPv4Network | IPv6Network]:
    raw = os.getenv("TRUSTED_PROXY_IPS", "")
    networks = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ip_network(entry, strict=False))
        except ValueError:
            continue
    return networks


def _client_ip(request: Request) -> str:
    client = request.client.host if request.client else "unknown"
    trusted = getattr(request.app.state, "trusted_proxies", [])
    if not trusted or client == "unknown":
        return client
    try:
        client_ip = ip_address(client)
    except ValueError:
        return client
    if not any(client_ip in net for net in trusted):
        return client
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return client
    return forwarded.split(",")[0].strip() or client


def _is_https(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        return forwarded_proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _should_enforce_https() -> bool:
    return os.getenv("ENFORCE_HTTPS", "").strip().lower() in {"1", "true", "yes"}


def _rate_limiter_from_env() -> _RateLimiter | None:
    try:
        limit = int(os.getenv("RATE_LIMIT_PER_MIN", "60"))
    except ValueError:
        limit = 60
    if limit <= 0:
        return None
    try:
        window_s = int(os.getenv("RATE_LIMIT_WINDOW_S", "60"))
    except ValueError:
        window_s = 60
    if window_s <= 0:
        window_s = 60
    return _RateLimiter(limit=limit, window_s=window_s)


def _require_api_key_configured() -> str:
    api_key = os.getenv("API_KEY")
    if not api_key:
        raise RuntimeError("API_KEY must be set to enable API access")
    return api_key


def _get_api_key(request: Request) -> str:
    cached = getattr(request.app.state, "api_key", None)
    if isinstance(cached, str) and cached:
        return cached
    api_key = _require_api_key_configured()
    request.app.state.api_key = api_key
    return api_key


def _require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    expected = _get_api_key(request)
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def _enforce_rate_limit(request: Request) -> None:
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return
    client = _client_ip(request)
    if not limiter.allow(client):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )


def _require_price_usd(ticker: str, trade_date: date) -> Decimal:
    """
    Resolve a share price from explicit overrides.

    This service intentionally fails fast if no override is configured to avoid
    misleading cost estimates.
    """

    env_keys = (
        f"PRICE_{ticker.upper()}_{trade_date.isoformat()}",
        f"PRICE_{ticker.upper()}",
        "PRICE_TEST_DEFAULT",
        "DEFAULT_SHARE_PRICE",
    )
    for key in env_keys:
        raw = os.getenv(key)
        if raw is None or not raw.strip():
            continue
        try:
            price = Decimal(raw)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Invalid price override in {key}") from exc
        if price <= 0:
            raise ValueError(f"Price override in {key} must be > 0")
        return price
    raise LookupError(
        "No price override configured. Set PRICE_<TICKER>_<YYYY-MM-DD>, "
        "PRICE_<TICKER>, DEFAULT_SHARE_PRICE, or PRICE_TEST_DEFAULT."
    )


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
            for k in (
                "cost_bps",
                "bps",
                "total_cost_bps",
                "total_bps",
                "impact_bps",
                "estimated_bps",
            ):
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
    app.state.api_key = _require_api_key_configured()
    app.state.rate_limiter = _rate_limiter_from_env()
    app.state.trusted_proxies = _trusted_proxy_networks()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if _should_enforce_https() and not _is_https(request):
            url = request.url.replace(scheme="https")
            return RedirectResponse(str(url), status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        if _is_https(request):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response

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
        _: None = Depends(_require_api_key),
        __: None = Depends(_enforce_rate_limit),
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
        _: None = Depends(_require_api_key),
        __: None = Depends(_enforce_rate_limit),
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

        try:
            price = _require_price_usd(ticker_norm, request.d)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(exc),
            ) from exc
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
        _: None = Depends(_require_api_key),
        __: None = Depends(_enforce_rate_limit),
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
