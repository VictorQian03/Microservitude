# cost_estimator/adapters/pg_repo.py
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Protocol
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from ..core.models import (
    CostRequestRecord,
    CostResult,
    ImpactModel,
    Liquidity,
    ModelCostBreakdown,
    ModelName,
    RequestStatus,
)
from ..core.ports import (
    CostRequestRepository as CostRequestRepositoryPort,
)
from ..core.ports import (
    LiquidityRepository as LiquidityRepositoryPort,
)
from ..core.ports import (
    ModelRepository as ModelRepositoryPort,
)

# ------------ DSN helpers ------------

_ENV_DSN_KEYS = ("DATABASE_URL", "DB_DSN", "POSTGRES_DSN")


def _normalize_dsn(dsn: str) -> str:
    return re.sub(r"^postgresql\+\w+://", "postgresql://", dsn)


def _env_dsn(env_var: Optional[str] = None) -> str:
    if env_var:
        raw = os.getenv(env_var)
        if not raw:
            raise RuntimeError(f"{env_var} is not set")
        return _normalize_dsn(raw)
    for key in _ENV_DSN_KEYS:
        raw = os.getenv(key)
        if raw:
            return _normalize_dsn(raw)
    raise RuntimeError("No DATABASE_URL/DB_DSN/POSTGRES_DSN found in env")


# ------------ Pool abstractions ------------


class _PoolLike(Protocol):
    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]: ...


@dataclass(slots=True)
class PgConfig:
    dsn: str


class PgPool:
    def __init__(self, cfg: PgConfig):
        self._pool = ConnectionPool(conninfo=_normalize_dsn(cfg.dsn), open=True)

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        with self._pool.connection() as c:
            orig = c.row_factory
            try:
                c.row_factory = dict_row
                yield c
            finally:
                c.row_factory = orig

    def close(self) -> None:
        self._pool.close()


class _FactoryPool:
    def __init__(self, factory: Callable[[], psycopg.Connection]):
        self._factory = factory

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        conn = self._factory()
        orig = conn.row_factory
        try:
            conn.row_factory = dict_row
            yield conn
        finally:
            conn.row_factory = orig


def _build_pool(
    *,
    pool: Optional[_PoolLike] = None,
    dsn: Optional[str] = None,
    connection_factory: Optional[Callable[[], psycopg.Connection]] = None,
) -> _PoolLike:
    if pool is not None:
        return pool
    if connection_factory is not None:
        return _FactoryPool(connection_factory)
    if dsn:
        return PgPool(PgConfig(dsn=_normalize_dsn(dsn)))
    return PgPool(PgConfig(dsn=_env_dsn()))


def _as_str(x) -> str:
    try:
        return x.value
    except AttributeError:
        return str(x)


def _jsonify_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Mapping):
        return {str(k): _jsonify_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonify_value(v) for v in value]
    return value


def _normalize_models_payload(models: Any) -> dict[str, dict[str, Any]]:
    if not models:
        return {}

    if hasattr(models, "items"):
        iterable = models.items()
    else:
        raise TypeError(f"Unsupported models container type: {type(models)!r}")

    normalized: dict[str, dict[str, Any]] = {}
    for key, payload in iterable:
        model_name = _as_str(key)
        if isinstance(payload, ModelCostBreakdown):
            data = payload.model_dump(mode="python")
        elif isinstance(payload, Mapping):
            data = dict(payload)
        else:
            raise TypeError(f"Unsupported model payload type: {type(payload)!r}")

        if "cost_usd" not in data and "usd" in data:
            data["cost_usd"] = data.pop("usd")
        if "cost_bps" not in data and "bps" in data:
            data["cost_bps"] = data.pop("bps")

        parameters = data.get("parameters") or data.get("params") or {}
        if not isinstance(parameters, Mapping):
            parameters = {}

        normalized_name = _as_str(data.get("name", model_name))
        if "cost_usd" not in data or "cost_bps" not in data:
            raise ValueError(f"Model payload for {normalized_name!r} missing cost fields")
        normalized_payload = {
            "name": normalized_name,
            "version": int(data.get("version", 0)),
            "parameters": {
                str(param_key): _jsonify_value(param_val)
                for param_key, param_val in parameters.items()
            },
            "cost_usd": _jsonify_value(data["cost_usd"]),
            "cost_bps": _jsonify_value(data["cost_bps"]),
        }
        normalized[normalized_name] = normalized_payload
    return normalized


# ------------ Liquidity repo ------------


class LiquidityRepository(LiquidityRepositoryPort):
    def __init__(
        self,
        pool: Optional[_PoolLike] = None,
        *,
        dsn: Optional[str] = None,
        connection_factory: Optional[Callable[[], psycopg.Connection]] = None,
    ):
        self.pool = _build_pool(pool=pool, dsn=dsn, connection_factory=connection_factory)

    def get_liquidity(self, ticker: str, d: date) -> Optional[Liquidity]:
        sql = """
        select dl.ticker, dl.d, dl.adv_usd
        from daily_liquidity dl
        where dl.ticker = %s and dl.d = %s
        """
        with self.pool.connection() as c:
            row = c.execute(sql, (ticker, d)).fetchone()
        if not row:
            return None
        return Liquidity(
            ticker=row["ticker"],
            d=row["d"],
            adv_usd=Decimal(row["adv_usd"]),
        )

    # Convenience for tests expecting a float
    def get_adv_for_ticker_date(self, ticker: str, d: str | date) -> Optional[float]:
        sql = "select adv_usd from daily_liquidity where ticker=%s and d=%s"
        with self.pool.connection() as c:
            row = c.execute(sql, (ticker, d)).fetchone()
        if not row or row["adv_usd"] is None:
            return None
        return float(row["adv_usd"])


# ------------ Model repo ------------


class ModelRepository(ModelRepositoryPort):
    def __init__(
        self,
        pool: Optional[_PoolLike] = None,
        *,
        dsn: Optional[str] = None,
        connection_factory: Optional[Callable[[], psycopg.Connection]] = None,
    ):
        self.pool = _build_pool(pool=pool, dsn=dsn, connection_factory=connection_factory)

    def get_active_models(self) -> Iterable[ImpactModel]:
        sql = """
        select name, version, params, active, created_at
        from impact_models
        where active = true
        order by created_at desc, version desc, name asc
        """
        with self.pool.connection() as c:
            rows = c.execute(sql).fetchall()
        for r in rows:
            yield ImpactModel(
                name=_as_str(r["name"]),
                version=int(r["version"]),
                params=r["params"] or {},
                active=bool(r["active"]),
                created_at=r["created_at"],
            )

    def get_latest_model(self, name: ModelName) -> Optional[ImpactModel]:
        sql = """
        select name, version, params, active, created_at
        from impact_models
        where name = %s and active = true
        order by version desc, created_at desc
        limit 1
        """
        with self.pool.connection() as c:
            row = c.execute(sql, (_as_str(name),)).fetchone()
        if not row:
            return None
        return ImpactModel(
            name=_as_str(row["name"]),
            version=int(row["version"]),
            params=row["params"] or {},
            active=bool(row["active"]),
            created_at=row["created_at"],
        )


# ------------ Cost repo ------------


class CostRepository(CostRequestRepositoryPort):
    def __init__(
        self,
        pool: Optional[_PoolLike] = None,
        *,
        dsn: Optional[str] = None,
        connection_factory: Optional[Callable[[], psycopg.Connection]] = None,
    ):
        self.pool = _build_pool(pool=pool, dsn=dsn, connection_factory=connection_factory)

    def create_request(self, request: CostRequestRecord) -> None:
        sql = """
        insert into cost_requests
            (id, ticker, shares, side, d, notional_usd, status, created_at)
        values
            (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        created_at = getattr(request, "created_at", None) or datetime.now(timezone.utc)
        with self.pool.connection() as c, c.transaction():
            c.execute(
                sql,
                (
                    str(request.id),
                    request.ticker,
                    int(request.shares),
                    _as_str(request.side),
                    request.d,
                    Decimal(request.notional_usd),
                    _as_str(request.status),
                    created_at,
                ),
            )

    def update_status(self, request_id: UUID, status: RequestStatus) -> None:
        sql = "update cost_requests set status = %s where id = %s"
        with self.pool.connection() as c, c.transaction():
            c.execute(sql, (_as_str(status), str(request_id)))

    def get_request(self, request_id: UUID | str) -> Optional[CostRequestRecord]:
        sql = """
        select id, ticker, shares, side, d, notional_usd, status, created_at
        from cost_requests
        where id = %s
        """
        with self.pool.connection() as c:
            row = c.execute(sql, (str(request_id),)).fetchone()
        if not row:
            return None
        return CostRequestRecord(
            id=row["id"] if isinstance(row["id"], UUID) else UUID(row["id"]),
            ticker=row["ticker"],
            shares=int(row["shares"]),
            side=_as_str(row["side"]),
            d=row["d"],
            notional_usd=Decimal(row["notional_usd"]),
            status=_as_str(row["status"]),
            created_at=row["created_at"],
        )

    # Accept either a CostResult DTO or keyword args used by tests.
    def save_result(self, *args, **kwargs) -> None:
        """
        Usage 1: save_result(CostResult(...))
        Usage 2: save_result(request_id=..., adv_usd=..., models=..., best_model=..., total_cost_usd=..., total_cost_bps=..., computed_at=?)
        """
        if args and isinstance(args[0], CostResult) and not kwargs:
            r: CostResult = args[0]
            request_id = str(r.request_id)
            adv_usd = None if getattr(r, "adv_usd", None) is None else Decimal(r.adv_usd)
            raw_models = r.models if getattr(r, "models", None) is not None else {}
            best_model = None if getattr(r, "best_model", None) is None else _as_str(r.best_model)
            total_cost_usd = Decimal(r.total_cost_usd)
            total_cost_bps = Decimal(r.total_cost_bps)
            computed_at = getattr(r, "computed_at", None) or datetime.now(timezone.utc)
        else:
            request_id = str(kwargs["request_id"])
            adv_val = kwargs.get("adv_usd")
            adv_usd = None if adv_val is None else Decimal(adv_val)
            raw_models = kwargs.get("models") or {}
            bm = kwargs.get("best_model")
            best_model = None if bm is None else _as_str(bm)
            total_cost_usd = Decimal(kwargs["total_cost_usd"])
            total_cost_bps = Decimal(kwargs["total_cost_bps"])
            computed_at = kwargs.get("computed_at") or datetime.now(timezone.utc)

        models = _normalize_models_payload(raw_models)

        sql = """
        insert into cost_results
            (request_id, adv_usd, models, best_model, total_cost_usd, total_cost_bps, computed_at)
        values
            (%s, %s, %s, %s, %s, %s, %s)
        on conflict (request_id) do update set
            adv_usd = excluded.adv_usd,
            models = excluded.models,
            best_model = excluded.best_model,
            total_cost_usd = excluded.total_cost_usd,
            total_cost_bps = excluded.total_cost_bps,
            computed_at = excluded.computed_at
        """
        with self.pool.connection() as c, c.transaction():
            c.execute(
                sql,
                (
                    request_id,
                    adv_usd,
                    Json(models),  # Wrap dict in Json() for proper serialization
                    best_model,
                    total_cost_usd,
                    total_cost_bps,
                    computed_at,
                ),
            )

    def get_result(self, request_id: UUID | str) -> Optional[CostResult]:
        sql = """
        select request_id, adv_usd, models, best_model, total_cost_usd, total_cost_bps, computed_at
        from cost_results
        where request_id = %s
        """
        with self.pool.connection() as c:
            row = c.execute(sql, (str(request_id),)).fetchone()
        if not row:
            return None
        return CostResult(
            request_id=row["request_id"]
            if isinstance(row["request_id"], UUID)
            else UUID(row["request_id"]),
            adv_usd=Decimal(row["adv_usd"]) if row["adv_usd"] is not None else None,
            models=row["models"] or {},
            best_model=_as_str(row["best_model"]) if row["best_model"] is not None else None,
            total_cost_usd=Decimal(row["total_cost_usd"]),
            total_cost_bps=Decimal(row["total_cost_bps"]),
            computed_at=row["computed_at"],
        )

    # Convenience used by integration test
    def save_request(
        self,
        *,
        ticker: str,
        shares: int,
        side: str,
        d: str | date,
        notional_usd: float | Decimal,
        request_id: Optional[str] = None,
        status: str = "queued",
    ) -> str:
        rid = request_id or str(UUID(bytes=os.urandom(16)))
        sql = """
        insert into cost_requests(id, ticker, shares, side, d, notional_usd, status, created_at)
        values (%s, %s, %s, %s, %s, %s, %s, now())
        """
        with self.pool.connection() as c, c.transaction():
            c.execute(
                sql,
                (
                    rid,
                    ticker,
                    int(shares),
                    _as_str(side),
                    d,
                    Decimal(notional_usd),
                    _as_str(status),
                ),
            )
        return rid


# ------------ Wiring helpers ------------


def make_pool_from_env(env_var: str = "DATABASE_URL") -> PgPool:
    dsn = _env_dsn(env_var)
    return PgPool(PgConfig(dsn=dsn))


@dataclass(slots=True)
class PgRepositories:
    liquidity: LiquidityRepository
    models: ModelRepository
    costs: CostRepository
    pool: PgPool

    @classmethod
    def from_env(cls, env_var: str = "DATABASE_URL") -> "PgRepositories":
        pool = make_pool_from_env(env_var)
        return cls(
            liquidity=LiquidityRepository(pool=pool),
            models=ModelRepository(pool=pool),
            costs=CostRepository(pool=pool),
            pool=pool,
        )
