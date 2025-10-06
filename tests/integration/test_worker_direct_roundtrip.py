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

    # FIXED: Pass function as string path instead of importing it
    # This is required when using JSONSerializer
    rq_queue.enqueue("cost_estimator.worker.worker.compute_cost", rid)
    rq_worker.work(burst=True)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT best_model, total_cost_bps, total_cost_usd FROM cost_results WHERE request_id=%s",
            (rid,),
        )
        row = cur.fetchone()
    assert row is not None
    best, bps, usd = row[0], float(row[1]), float(row[2])
    assert best in {"sqrt", "pct_adv"}
    assert bps > 0 and usd > 0
    # Basic sanity to guard regressions
    assert bps < 50.0
    assert math.isfinite(usd)