import pytest

PgRepo = pytest.importorskip("cost_estimator.adapters.pg_repo")


def test_liquidity_repo_get_adv(db_conn):
    repo = PgRepo.LiquidityRepository(dsn=None, connection_factory=lambda: db_conn)
    adv = repo.get_adv_for_ticker_date("AAPL", "2025-09-19")
    assert adv == 5_000_000_000.0


def test_model_repo_active_models(db_conn):
    repo = PgRepo.ModelRepository(dsn=None, connection_factory=lambda: db_conn)
    models = repo.get_active_models()
    names = {m.name for m in models}
    assert {"pct_adv", "sqrt"} <= names


def test_cost_repo_roundtrip(db_conn):
    repo = PgRepo.CostRepository(dsn=None, connection_factory=lambda: db_conn)
    rid = repo.save_request(
        ticker="AAPL", shares=100_000, side="buy", d="2025-09-19", notional_usd=20_000_000.0
    )
    assert rid

    # Save result with proper ModelCostBreakdown structure
    repo.save_result(
        request_id=rid,
        adv_usd=5_000_000_000.0,
        models={
            "pct_adv": {
                "name": "pct_adv",
                "version": 1,
                "parameters": {"c": 0.5, "cap": 0.1},
                "cost_usd": 40_000.0,
                "cost_bps": 20.0,
            }
        },
        best_model="pct_adv",
        total_cost_bps=20.0,
        total_cost_usd=40_000.0,
    )

    got = repo.get_result(rid)
    assert got and got.best_model == "pct_adv"
    assert got.total_cost_bps == 20.0
    assert got.total_cost_usd == 40_000.0
    assert "pct_adv" in got.models
    assert got.models["pct_adv"].cost_bps == 20.0
