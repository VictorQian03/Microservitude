from __future__ import annotations

from decimal import Decimal

import pytest

from cost_estimator.core.calculators import CostCalculationError
from cost_estimator.worker import worker as worker_module


def test_pct_adv_requires_c_param() -> None:
    with pytest.raises(CostCalculationError):
        worker_module._compute_pct_adv(
            notional_usd=Decimal("100"),
            adv_usd=Decimal("1000"),
            params={},
        )


def test_sqrt_requires_a_and_b_params() -> None:
    with pytest.raises(CostCalculationError):
        worker_module._compute_sqrt(
            shares=100,
            notional_usd=Decimal("10000"),
            adv_usd=Decimal("500000"),
            params={"A": Decimal("10")},
        )
    with pytest.raises(CostCalculationError):
        worker_module._compute_sqrt(
            shares=100,
            notional_usd=Decimal("10000"),
            adv_usd=Decimal("500000"),
            params={"B": Decimal("1")},
        )
