async def test_health(http_client):
    r = await http_client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") in {"ok", "healthy", "alive"}


async def test_estimate_unknown_ticker_404(http_client, rq_worker):
    body = {"ticker": "ZZZZ", "shares": 1000, "side": "buy", "date": "2025-09-19"}
    r = await http_client.post("/estimate", json=body)
    assert r.status_code in {400, 404, 422}


async def test_estimate_bad_side_422(http_client):
    body = {"ticker": "AAPL", "shares": 1000, "side": "hold", "date": "2025-09-19"}
    r = await http_client.post("/estimate", json=body)
    assert r.status_code in {400, 422}
