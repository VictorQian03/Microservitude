import math
import uuid
from datetime import date


def test_worker_can_compute_when_row_preinserted(db_conn, rq_queue, rq_worker):
    # Insert a queued request row directly to DB, then enqueue task with that id.
    rid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cost_requests(id, ticker, shares, side, d, notional_usd, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'queued')
            """,
            (rid, "AAPL", 100000, "buy", date(2025, 9, 19), 20_000_000.0),
        )
    db_conn.commit()

    rq_queue.enqueue("cost_estimator.worker.worker.compute_cost", rid)
    rq_worker.work(burst=True)

    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT models, best_model, total_cost_bps, total_cost_usd
            FROM cost_results
            WHERE request_id=%s
            """,
            (rid,),
        )
        row = cur.fetchone()
    assert row is not None
    models_payload, best, bps, usd = row
    assert isinstance(models_payload, dict)
    assert best in {"sqrt", "pct_adv"}
    assert best in models_payload
    breakdown = models_payload[best]
    assert {"name", "version", "parameters", "cost_usd", "cost_bps"} <= set(breakdown)
    assert breakdown["name"] == best
    assert isinstance(breakdown["parameters"], dict)
    assert float(breakdown["cost_bps"]) > 0
    assert float(breakdown["cost_usd"]) > 0
    bps = float(bps)
    usd = float(usd)
    assert bps > 0 and usd > 0
    assert bps < 50.0
    assert math.isfinite(usd)
