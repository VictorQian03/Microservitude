# cost_estimator/worker/worker.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Tuple

from cost_estimator.adapters.pg_repo import PgRepositories
from cost_estimator.core.calculators import (
    calculate_pct_adv_cost,
    calculate_sqrt_cost,
    CostCalculationError,
)
from cost_estimator.core.models import CostRequestRecord


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _dec(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def _compute_pct_adv(
    *, notional_usd: Decimal, adv_usd: Decimal, params: Dict
) -> Tuple[Decimal, Decimal]:
    c = _dec(params.get("c", 0.5))
    cap = params.get("cap", 0.1)
    cap_dec = _dec(cap) if cap is not None else None
    return calculate_pct_adv_cost(
        notional_usd=notional_usd, adv_usd=adv_usd, c=c, cap=cap_dec
    )


def _compute_sqrt(
    *, shares: int, notional_usd: Decimal, adv_usd: Decimal, params: Dict
) -> Tuple[Decimal, Decimal]:
    # price hint from request if present, else implied by notional/shares
    price_env = os.getenv("PRICE_TEST_DEFAULT")
    if price_env:
        price = _dec(price_env)
    else:
        price = notional_usd / _dec(shares)

    a = _dec(params.get("A", 300.0))
    b = _dec(params.get("B", 0.0))
    adv_shares = adv_usd / price
    return calculate_sqrt_cost(
        shares=shares, adv_shares=adv_shares, price=price, a=a, b=b
    )


def compute_cost(request_id: str) -> bool:
    """
    RQ job entrypoint.
    - Load request, liquidity, and active models from Postgres.
    - Compute model costs using core.calculators.
    - Persist result and mark request status.
    Returns True on success, False on handled failure.
    """
    repos = PgRepositories.from_env()
    costs = repos.costs
    models_repo = repos.models
    liq_repo = repos.liquidity

    try:
        req = costs.get_request(request_id)
        if not isinstance(req, CostRequestRecord):
            costs.update_status(request_id, "error")
            return False

        ticker = req.ticker
        d_str = req.d.isoformat() if hasattr(req.d, "isoformat") else str(req.d)
        shares = int(req.shares)
        notional = _dec(req.notional_usd)

        adv_usd = _dec(liq_repo.get_adv_for_ticker_date(ticker, d_str))

        per_model: Dict[str, Dict[str, float]] = {}
        for m in models_repo.get_active_models():
            name = str(m.name).lower()
            params = m.params or {}

            if name == "pct_adv":
                usd, bps = _compute_pct_adv(
                    notional_usd=notional, adv_usd=adv_usd, params=params
                )
            elif name == "sqrt":
                usd, bps = _compute_sqrt(
                    shares=shares, notional_usd=notional, adv_usd=adv_usd, params=params
                )
            else:
                continue

            per_model[name] = {"usd": float(usd), "bps": float(bps)}

        if not per_model:
            costs.update_status(req.id, "error")
            return False

        best_model = min(per_model.items(), key=lambda kv: kv[1]["bps"])
        best_name, best_vals = best_model
        total_cost_usd = best_vals["usd"]
        total_cost_bps = best_vals["bps"]

        costs.save_result(
            request_id=str(req.id),
            adv_usd=float(adv_usd),
            models=per_model,
            best_model=best_name,
            total_cost_usd=total_cost_usd,
            total_cost_bps=total_cost_bps,
            computed_at=_now_utc(),
        )
        costs.update_status(req.id, "done")
        return True

    except CostCalculationError:
        try:
            costs.update_status(request_id, "error")
        except Exception:
            pass
        return False
    except Exception:
        try:
            costs.update_status(request_id, "error")
        except Exception:
            pass
        return False


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: python -m cost_estimator.worker.worker <request_id>")
        raise SystemExit(2)
    print("OK" if compute_cost(sys.argv[1]) else "ERROR")
