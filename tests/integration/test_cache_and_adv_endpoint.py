async def test_adv_endpoint_caches(redis_client, http_client):
    key = f"adv:{'AAPL'}:{'2025-09-19'}"
    assert redis_client.get(key) is None

    r = await http_client.get("/adv/AAPL", params={"date": "2025-09-19"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["ticker"] == "AAPL"
    assert float(payload["adv_usd"]) == 5_000_000_000.0

    # Cache set
    cached = redis_client.get(key)
    assert cached is not None
    assert abs(float(cached) - 5_000_000_000.0) < 1e-6
