from decimal import Decimal, getcontext
from typing import Tuple

BPS = Decimal(10_000)
ONE_BPS = Decimal("1e-4")
ZERO = Decimal(0)
ONE = Decimal(1)


class CostCalculationError(ValueError):
    pass


def calculate_pct_adv_cost(
    *, notional_usd: Decimal, adv_usd: Decimal, c: Decimal, cap: Decimal | None = None
) -> Tuple[Decimal, Decimal]:
    if notional_usd <= ZERO:
        raise CostCalculationError("Notional must be positive.")
    if adv_usd <= ZERO:
        raise CostCalculationError("ADV must be positive.")
    if cap is not None and not (ZERO < cap <= ONE):
        raise CostCalculationError("Cap must be in (0, 1].")

    p = notional_usd / adv_usd
    if cap is not None:
        p = cap if p > cap else p

    impact = c * p
    return notional_usd * impact, impact * BPS


def calculate_sqrt_cost(
    *, shares: int | Decimal, adv_shares: Decimal, price: Decimal, a: Decimal, b: Decimal
) -> Tuple[Decimal, Decimal]:
    s = shares if isinstance(shares, Decimal) else Decimal(shares)
    if s <= ZERO:
        raise CostCalculationError("Shares must be positive.")
    if adv_shares <= ZERO:
        raise CostCalculationError("ADV shares must be positive.")
    if price <= ZERO:
        raise CostCalculationError("Price must be positive.")

    ctx = getcontext()
    p = s / adv_shares
    impact_bps = a * p.sqrt(context=ctx) + b
    return s * price * impact_bps * ONE_BPS, impact_bps
