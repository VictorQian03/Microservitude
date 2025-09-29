from decimal import Decimal

import pytest

from cost_estimator.core.calculators import (
    CostCalculationError,
    calculate_pct_adv_cost,
    calculate_sqrt_cost,
)


class TestPctAdvCost:
    def test_basic_pct_adv_cost(self) -> None:
        cost_usd, cost_bps = calculate_pct_adv_cost(
            notional_usd=Decimal("1000000"),
            adv_usd=Decimal("10000000"),
            c=Decimal("0.5"),
            cap=Decimal("0.1"),
        )

        assert cost_usd == Decimal("50000")
        assert cost_bps == Decimal("500")

    def test_cap_applied(self) -> None:
        cost_usd, cost_bps = calculate_pct_adv_cost(
            notional_usd=Decimal("1000000"),
            adv_usd=Decimal("2000000"),
            c=Decimal("0.25"),
            cap=Decimal("0.05"),
        )

        assert cost_usd == Decimal("12500")
        assert cost_bps == Decimal("125")

    def test_invalid_adv_raises(self) -> None:
        with pytest.raises(CostCalculationError):
            calculate_pct_adv_cost(
                notional_usd=Decimal("1000000"),
                adv_usd=Decimal("0"),
                c=Decimal("0.5"),
            )

    def test_zero_notional_raises(self) -> None:
        with pytest.raises(CostCalculationError):
            calculate_pct_adv_cost(
                notional_usd=Decimal("0"),
                adv_usd=Decimal("100"),
                c=Decimal("0.5"),
            )

    def test_nonpositive_cap_raises(self) -> None:
        with pytest.raises(CostCalculationError):
            calculate_pct_adv_cost(
                notional_usd=Decimal("100"),
                adv_usd=Decimal("1000"),
                c=Decimal("0.5"),
                cap=Decimal("0"),
            )


class TestSqrtCost:
    def test_basic_sqrt_cost(self) -> None:
        cost_usd, cost_bps = calculate_sqrt_cost(
            shares=100000,
            adv_shares=Decimal("1000000"),
            price=Decimal("10"),
            a=Decimal("50"),
            b=Decimal("10"),
        )

        assert cost_usd.quantize(Decimal("0.01")) == Decimal("2581.14")
        assert cost_bps.quantize(Decimal("0.0001")) == Decimal("25.8114")

    def test_invalid_adv_shares_raises(self) -> None:
        with pytest.raises(CostCalculationError):
            calculate_sqrt_cost(
                shares=100000,
                adv_shares=Decimal("0"),
                price=Decimal("10"),
                a=Decimal("50"),
                b=Decimal("10"),
            )

    def test_invalid_price_raises(self) -> None:
        with pytest.raises(CostCalculationError):
            calculate_sqrt_cost(
                shares=100,
                adv_shares=Decimal("100000"),
                price=Decimal("0"),
                a=Decimal("25"),
                b=Decimal("5"),
            )

    def test_zero_shares_raises(self) -> None:
        with pytest.raises(CostCalculationError):
            calculate_sqrt_cost(
                shares=0,
                adv_shares=Decimal("100000"),
                price=Decimal("10"),
                a=Decimal("25"),
                b=Decimal("5"),
            )

    def test_negative_shares_raises(self) -> None:
        with pytest.raises(CostCalculationError):
            calculate_sqrt_cost(
                shares=-100,
                adv_shares=Decimal("100000"),
                price=Decimal("10"),
                a=Decimal("25"),
                b=Decimal("5"),
            )

    def test_high_participation(self) -> None:
        shares = Decimal("5000000")
        adv_shares = Decimal("1000000")
        price = Decimal("20")
        a = Decimal("50")
        b = Decimal("10")

        cost_usd, cost_bps = calculate_sqrt_cost(
            shares=int(shares),
            adv_shares=adv_shares,
            price=price,
            a=a,
            b=b,
        )

        expected_participation = (shares / adv_shares).sqrt()
        expected_bps = a * expected_participation + b
        expected_cost_usd = (expected_bps / Decimal("10000")) * (shares * price)

        assert cost_bps == expected_bps
        assert cost_usd == expected_cost_usd
