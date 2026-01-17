import uuid

import psycopg
import pytest


def test_schema_and_seeds(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM symbols WHERE ticker = 'AAPL'")
        assert cur.fetchone()[0] == 1

        cur.execute("SELECT adv_usd FROM daily_liquidity WHERE ticker='AAPL' AND d='2025-09-19'")
        adv = float(cur.fetchone()[0])
        assert adv == 5_000_000_000.0

        cur.execute(
            "SELECT name, version, params FROM impact_models WHERE active = TRUE ORDER BY name"
        )
        rows = cur.fetchall()
        names = [r[0] for r in rows]
        assert set(names) == {"pct_adv", "sqrt"}


def test_daily_liquidity_constraints(db_conn):
    with db_conn.cursor() as cur:
        with pytest.raises(psycopg.Error):
            cur.execute(
                "INSERT INTO daily_liquidity(ticker, d, adv_usd) VALUES (%s, %s, %s)",
                ("AAPL", "2025-09-20", None),
            )
        db_conn.rollback()

        with pytest.raises(psycopg.Error):
            cur.execute(
                "INSERT INTO daily_liquidity(ticker, d, adv_usd) VALUES (%s, %s, %s)",
                ("AAPL", "2025-09-21", 0),
            )
        db_conn.rollback()


def test_cost_request_constraints(db_conn):
    with db_conn.cursor() as cur:
        with pytest.raises(psycopg.Error):
            cur.execute(
                """
                INSERT INTO cost_requests(id, ticker, shares, side, d, notional_usd, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), "MISSING", 10, "buy", "2025-09-19", 1000, "queued"),
            )
        db_conn.rollback()

        with pytest.raises(psycopg.Error):
            cur.execute(
                """
                INSERT INTO cost_requests(id, ticker, shares, side, d, notional_usd, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), "AAPL", 10, "buy", "2025-09-19", 0, "queued"),
            )
        db_conn.rollback()


def test_cost_results_totals_required(db_conn):
    request_id = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cost_requests(id, ticker, shares, side, d, notional_usd, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (request_id, "AAPL", 10, "buy", "2025-09-19", 1000, "queued"),
        )
        db_conn.commit()

        with pytest.raises(psycopg.Error):
            cur.execute(
                """
                INSERT INTO cost_results(request_id, adv_usd, models, best_model, total_cost_usd, total_cost_bps)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (request_id, 1000, "{}", "pct_adv", None, None),
            )
        db_conn.rollback()
