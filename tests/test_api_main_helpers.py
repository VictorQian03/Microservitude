from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

from cost_estimator.api.main import (
    AppDependencies,
    _extract_cost_bps,
    _infer_best_model_from_models,
)


@dataclass
class _Closer:
    raises: bool = False
    calls: int = 0

    def close(self) -> None:
        self.calls += 1
        if self.raises:
            raise RuntimeError("close failed")


def test_app_dependencies_shutdown_closes_all_resources() -> None:
    repo_pool = _Closer()
    cache_conn = _Closer()
    queue_conn = _Closer()
    repos = SimpleNamespace(costs=object(), liquidity=object(), pool=repo_pool)
    cache = SimpleNamespace(_r=cache_conn)
    queue = SimpleNamespace(_redis=queue_conn)

    deps = AppDependencies(repos=repos, cache=cache, queue=queue)
    deps.shutdown()

    assert repo_pool.calls == 1
    assert cache_conn.calls == 1
    assert queue_conn.calls == 1


def test_app_dependencies_shutdown_swallows_close_errors() -> None:
    repo_pool = _Closer(raises=True)
    cache_conn = _Closer(raises=True)
    queue_conn = _Closer(raises=True)
    repos = SimpleNamespace(costs=object(), liquidity=object(), pool=repo_pool)
    cache = SimpleNamespace(_r=cache_conn)
    queue = SimpleNamespace(_redis=queue_conn)

    deps = AppDependencies(repos=repos, cache=cache, queue=queue)
    deps.shutdown()

    assert repo_pool.calls == 1
    assert cache_conn.calls == 1
    assert queue_conn.calls == 1


def test_extract_cost_bps_from_various_payloads() -> None:
    class CostObject:
        def __init__(self, value: Decimal | str) -> None:
            self.cost_bps = value

    assert _extract_cost_bps(CostObject("10.5")) == Decimal("10.5")
    assert _extract_cost_bps({"total_cost_bps": "5.1"}) == Decimal("5.1")
    assert _extract_cost_bps(("foo", {"impact_bps": Decimal("3.2")})) == Decimal("3.2")

    assert _extract_cost_bps({"cost_bps": "not-a-number"}) is None
    assert _extract_cost_bps(object()) is None


def test_infer_best_model_from_mapping_and_sequences() -> None:
    mapping_models = {
        "alpha": {"cost_bps": "4.0"},
        "beta": {"cost_bps": "3.5"},
        "gamma": {"cost_bps": "6.0"},
    }
    assert _infer_best_model_from_models(mapping_models) == "beta"

    sequence_models = [
        {"name": "x", "total_cost_bps": "7.5"},
        {"name": "y", "total_cost_bps": "2.5"},
        {"name": "z", "total_cost_bps": "5.0"},
    ]
    assert _infer_best_model_from_models(sequence_models) == "y"

    tuple_models = [
        ("one", {"bps": "3.3"}),
        ("two", {"bps": "1.1"}),
    ]
    assert _infer_best_model_from_models(tuple_models) == "two"

    assert _infer_best_model_from_models([{"name": "bad", "cost_bps": "oops"}]) is None
