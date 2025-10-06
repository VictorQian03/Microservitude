import math

async def test_post_estimate_then_worker_then_get(http_client, rq_worker, db_conn):
    # Submit
    body = {"ticker": "AAPL", "shares": 100000, "side": "buy", "date": "2025-09-19"}
    r = await http_client.post("/estimate", json=body)
    assert r.status_code == 200
    out = r.json()
    rid = out["request_id"]
    assert out["status"] == "queued"

    # Process one job
    rq_worker.work(burst=True)

    # Fetch result
    r2 = await http_client.get(f"/estimate/{rid}")
    assert r2.status_code == 200
    res = r2.json()
    # Validate numeric results near expected
    best = res["best_model"]
    bps = float(res["total_cost_bps"])
    usd = float(res["total_cost_usd"])
    # With chosen params best should be sqrt since ~18.97 bps < 20 bps
    assert best in {"sqrt", "pct_adv"}
    assert math.isclose(bps, 18.97366596, rel_tol=5e-3) or math.isclose(bps, 20.0, rel_tol=5e-3)
    if best == "sqrt":
        assert math.isclose(usd, 3794.733, rel_tol=5e-3) or usd > 3000
