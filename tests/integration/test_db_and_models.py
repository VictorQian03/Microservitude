import psycopg

def test_schema_and_seeds(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM symbols WHERE ticker = 'AAPL'")
        assert cur.fetchone()[0] == 1

        cur.execute("SELECT adv_usd FROM daily_liquidity WHERE ticker='AAPL' AND d='2025-09-19'")
        adv = float(cur.fetchone()[0])
        assert adv == 5_000_000_000.0

        cur.execute("SELECT name, version, params FROM impact_models WHERE active = TRUE ORDER BY name")
        rows = cur.fetchall()
        names = [r[0] for r in rows]
        assert set(names) == {"pct_adv", "sqrt"}
