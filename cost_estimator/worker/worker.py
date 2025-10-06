# cost_estimator/worker/worker.py
from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict

from cost_estimator.adapters.pg_repo import PgRepositories
from cost_estimator.core.models import CostRequestRecord


def _now_utc():
    return datetime.now(timezone.utc)


def _to_float(x) -> float:
    if x is None:
        return None  # type: ignore[return-value]
    return float(x) if not isinstance(x, (int, float)) else x  # tolerate Decimal


def _compute_pct_adv(notional_usd: float, adv_usd: float, params: Dict) -> Dict[str, float]:
    c = float(params.get("c", 0.5))
    cap = float(params.get("cap", 0.1))
    q = min(notional_usd / adv_usd, cap) if adv_usd and adv_usd > 0 else 0.0
    cost_usd = c * q * notional_usd
    cost_bps = 1e4 * (cost_usd / notional_usd) if notional_usd > 0 else 0.0
    return {"usd": cost_usd, "bps": cost_bps}


def _compute_sqrt(shares: int, notional_usd: float, adv_usd: float, params: Dict, price_hint: float) -> Dict[str, float]:
    A = float(params.get("A", 300.0))
    B = float(params.get("B", 0.0))
    price = float(params.get("price_hint", price_hint))
    if price <= 0:
        # derive from notional if possible
        price = notional_usd / shares if shares > 0 else 1.0
    adv_shares = adv_usd / price if price > 0 else 0.0
    ratio = shares / adv_shares if adv_shares > 0 else 0.0
    cost_bps = A * math.sqrt(ratio) + B if ratio > 0 else max(B, 0.0)
    cost_usd = notional_usd * cost_bps / 1e4 if notional_usd > 0 else 0.0
    return {"usd": cost_usd, "bps": cost_bps}


def compute_cost(request_id: str) -> bool:
    """
    RQ task. Loads request, computes per-model costs, writes result, marks done.
    Returns True on success, False on handled error.
    """
    repos = PgRepositories.from_env()  # uses DATABASE_URL / DB_DSN / POSTGRES_DSN
    costs = repos.costs
    models_repo = repos.models
    liq_repo = repos.liquidity

    try:
        req = costs.get_request(request_id)
        if not isinstance(req, CostRequestRecord):
            # Accept UUID or str in repo, but we expect DTO back.
            return False

        # Inputs
        ticker = req.ticker
        d = req.d
        shares = int(req.shares)
        notional = _to_float(req.notional_usd)
        if notional is None or notional <= 0:
            costs.update_status(req.id, "error")
            return False

        liq = liq_repo.get_liquidity(ticker, d)
        adv_usd = _to_float(liq.adv_usd) if liq and liq.adv_usd is not None else None
        if adv_usd is None or adv_usd <= 0:
            costs.update_status(req.id, "error")
            return False

        # Price hint: env or implied from notional/shares
        env_price = os.getenv("PRICE_TEST_DEFAULT")
        price_hint = float(env_price) if env_price else (notional / shares if shares > 0 else 100.0)

        # Active models - build proper ModelCostBreakdown structure
        per_model = {}
        for m in models_repo.get_active_models():
            name = str(m.name).lower()
            if name == "pct_adv":
                result = _compute_pct_adv(notional, adv_usd, m.params or {})
                per_model[name] = {
                    "name": name,
                    "version": m.version,
                    "parameters": {k: float(v) for k, v in (m.params or {}).items()},
                    "cost_usd": result["usd"],
                    "cost_bps": result["bps"]
                }
            elif name == "sqrt":
                result = _compute_sqrt(shares, notional, adv_usd, m.params or {}, price_hint)
                per_model[name] = {
                    "name": name,
                    "version": m.version,
                    "parameters": {k: float(v) for k, v in (m.params or {}).items()},
                    "cost_usd": result["usd"],
                    "cost_bps": result["bps"]
                }
            else:
                # Unknown model type, skip
                continue

        if not per_model:
            costs.update_status(req.id, "error")
            return False

        # Choose best by bps (lower is better)
        best_model, best = min(per_model.items(), key=lambda kv: kv[1]["cost_bps"])
        total_cost_bps = best["cost_bps"]
        total_cost_usd = best["cost_usd"]

        # Persist result
        costs.save_result(
            request_id=str(req.id),
            adv_usd=adv_usd,
            models=per_model,
            best_model=best_model,
            total_cost_usd=total_cost_usd,
            total_cost_bps=total_cost_bps,
            computed_at=_now_utc(),
        )
        costs.update_status(req.id, "done")
        return True

    except Exception:
        # Best-effort error marking
        try:
            costs.update_status(request_id, "error")  # type: ignore[arg-type]
        except Exception:
            pass
        return False


# Optional: allow manual CLI run for debugging
if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("usage: python -m cost_estimator.worker.worker <request_id>")
        sys.exit(2)
    ok = compute_cost(sys.argv[1])
    print("OK" if ok else "ERROR")