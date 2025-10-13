from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import pytest

from cost_estimator.adapters import redis_cache
from cost_estimator.adapters.redis_cache import (
    RedisCache,
    _from_json,
    _json_default,
    _to_json,
)
from cost_estimator.core.models import CachedADV


@dataclass
class _ExampleDataclass:
    value: int


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.set_calls: list[tuple[str, object]] = []
        self.setex_calls: list[tuple[str, int, object]] = []

    def get(self, key: str) -> object | None:
        return self.store.get(key)

    def set(self, key: str, value: object) -> None:
        self.store[key] = value
        self.set_calls.append((key, value))

    def setex(self, key: str, ttl: int, value: object) -> None:
        self.store[key] = value
        self.setex_calls.append((key, ttl, value))


def _model_dump(payload: CachedADV) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload.dict()


def test_json_default_supports_date_datetime_decimal_and_dataclass() -> None:
    today = date(2024, 5, 1)
    now = datetime(2024, 5, 1, 12, 30, 0)
    amount = Decimal("123.45")
    payload = _ExampleDataclass(value=42)

    assert _json_default(today) == today.isoformat()
    assert _json_default(now) == now.isoformat()
    assert _json_default(amount) == str(amount)
    assert _json_default(payload) == {"value": 42}

    with pytest.raises(TypeError):
        _json_default(object())


def test_to_json_serializes_cached_adv_via_pydantic() -> None:
    payload = CachedADV(ticker="AAPL", d=date(2024, 5, 1), adv_usd=Decimal("1000"))

    if hasattr(payload, "model_dump_json"):
        expected = payload.model_dump_json()
    else:
        expected = payload.json()

    assert _to_json(payload) == expected


def test_from_json_round_trips_cached_adv(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = CachedADV(ticker="MSFT", d=date(2024, 5, 2), adv_usd=Decimal("5000"))
    raw = _to_json(payload)

    restored = _from_json(raw)

    assert restored.ticker == payload.ticker
    assert restored.d == payload.d
    assert restored.adv_usd == payload.adv_usd
    assert isinstance(restored.cached_at, datetime)


def test_from_json_fallback_handles_stringified_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(redis_cache.CachedADV, "model_validate_json", classmethod(_raise), raising=False)
    monkeypatch.setattr(redis_cache.CachedADV, "parse_raw", classmethod(_raise), raising=False)

    raw_data = {
        "ticker": "TSLA",
        "d": "2024-05-03",
        "adv_usd": "1234.56",
        "cached_at": "2024-05-03T10:00:00",
    }
    raw = json.dumps(raw_data)

    restored = _from_json(raw)

    assert restored.ticker == "TSLA"
    assert restored.d == date(2024, 5, 3)
    assert restored.adv_usd == Decimal("1234.56")


def test_get_adv_returns_none_for_missing_key() -> None:
    client = _FakeRedis()
    cache = RedisCache(client=client)

    assert cache.get_adv("aapl", date(2024, 5, 1)) is None


def test_get_adv_decodes_json_strings_and_bytes() -> None:
    client = _FakeRedis()
    cache = RedisCache(client=client)
    lookup_date = date(2024, 5, 4)
    payload = CachedADV(ticker="NFLX", d=lookup_date, adv_usd=Decimal("2500"))
    json_payload = _to_json(payload)
    key = cache._key("NFLX", lookup_date)

    client.store[key] = json_payload
    restored = cache.get_adv("NFLX", lookup_date)
    assert restored is not None
    assert _model_dump(restored) == _model_dump(payload)

    client.store[key] = json_payload.encode("utf-8")
    restored_bytes = cache.get_adv("NFLX", lookup_date)
    assert restored_bytes is not None
    assert _model_dump(restored_bytes) == _model_dump(payload)


def test_set_adv_uses_set_and_setex(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeRedis()
    cache = RedisCache(client=client)
    payload = CachedADV(ticker="GOOG", d=date(2024, 5, 5), adv_usd=Decimal("4000"))
    key = cache._key("GOOG", payload.d)

    cache.set_adv(payload)
    assert client.set_calls == [(key, _to_json(payload))]
    assert client.setex_calls == []

    client.set_calls.clear()
    cache.set_adv(payload, ttl_seconds=60)
    assert client.setex_calls == [(key, 60, _to_json(payload))]
