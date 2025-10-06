async def test_adv_cache_key_written(redis_client, http_client):
    key = "adv:AAPL:2025-09-19"
    redis_client.delete(key)
    r = await http_client.get("/adv/AAPL", params={"date": "2025-09-19"})
    assert r.status_code == 200
    val = redis_client.get(key)
    assert val is not None
    assert abs(float(val) - 5_000_000_000.0) < 1e-6
