# cost_estimator/adapters/redis_cache.py
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from os import getenv
from typing import Optional

from redis import Redis

from ..core.models import CachedADV
from ..core.ports import LiquidityCache


def _json_default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    if is_dataclass(o):
        return asdict(o)
    raise TypeError(f"Not JSON serializable: {type(o)}")


def _to_json(payload: CachedADV) -> str:
    # Pydantic v2
    if hasattr(payload, "model_dump_json"):
        return payload.model_dump_json()  # type: ignore[attr-defined]
    # Pydantic v1
    if hasattr(payload, "json"):
        return payload.json()  # type: ignore[attr-defined]
    # Dataclass or plain object fallback
    return json.dumps(payload, default=_json_default)


def _from_json(s: str) -> CachedADV:
    # Pydantic v2
    if hasattr(CachedADV, "model_validate_json"):
        try:
            return CachedADV.model_validate_json(s)  # type: ignore[attr-defined]
        except Exception:
            pass
    # Pydantic v1
    if hasattr(CachedADV, "parse_raw"):
        try:
            return CachedADV.parse_raw(s)  # type: ignore[attr-defined]
        except Exception:
            pass
    # Fallback
    d = json.loads(s)
    if "d" in d and isinstance(d["d"], str):
        d["d"] = date.fromisoformat(d["d"])
    if "adv_usd" in d and isinstance(d["adv_usd"], str):
        d["adv_usd"] = Decimal(d["adv_usd"])
    return CachedADV(**d)


class RedisCache(LiquidityCache):
    """Redis-backed implementation of LiquidityCache."""

    def __init__(
        self,
        url: Optional[str] = None,
        client: Optional[Redis] = None,
        namespace: str = "adv",
        decode_responses: bool = True,
    ) -> None:
        if client is None:
            url = url or getenv("REDIS_URL", "redis://localhost:6379/0")
            client = Redis.from_url(url, decode_responses=decode_responses)
        self._r = client
        self._ns = namespace

    def _key(self, ticker: str, d: date) -> str:
        return f"{self._ns}:{ticker.upper()}:{d.isoformat()}"

    def get_adv(self, ticker: str, d: date) -> Optional[CachedADV]:
        val = self._r.get(self._key(ticker, d))
        if val is None:
            return None
        if isinstance(val, bytes):
            val = val.decode("utf-8")
        return _from_json(val)

    def set_adv(self, payload: CachedADV, ttl_seconds: int | None = None) -> None:
        key = self._key(payload.ticker, payload.d)
        data = _to_json(payload)
        if ttl_seconds is None:
            self._r.set(key, data)
        else:
            self._r.setex(key, ttl_seconds, data)


def make_redis_cache_from_env(env_var: str = "REDIS_URL", namespace: str = "adv") -> RedisCache:
    url = getenv(env_var, "redis://localhost:6379/0")
    return RedisCache(url=url, namespace=namespace)
