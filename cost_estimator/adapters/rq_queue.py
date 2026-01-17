# cost_estimator/adapters/rq_queue.py
from __future__ import annotations

from dataclasses import dataclass
from os import getenv
from typing import Optional
from urllib.parse import urlparse

from redis import Redis
from rq import Queue, Retry
from rq.serializers import JSONSerializer

from ..core.models import CostRequestRecord
from ..core.ports import CostEstimationQueue


@dataclass(slots=True)
class RQConfig:
    redis_url: str
    queue_name: str = "estimates"
    job_func_path: str = "cost_estimator.worker.worker.compute_cost"
    job_timeout_s: int = 120
    result_ttl_s: int = 0  # results live in Postgres, not RQ
    failure_ttl_s: int = 86400  # 1 day
    retry_max: int = 3
    retry_intervals: tuple[int, ...] = (10, 30, 90)


def _cfg_from_env() -> RQConfig:
    url = getenv("RQ_REDIS_URL") or getenv("REDIS_URL")
    if url:
        _validate_redis_url(url)
    elif _app_env() == "prod":
        raise RuntimeError("RQ_REDIS_URL or REDIS_URL must be set when APP_ENV=prod")
    else:
        url = "redis://localhost:6379/0"
    name = getenv("RQ_QUEUE_NAME", "estimates")
    func = getenv("RQ_JOB_FUNC", "cost_estimator.worker.worker.compute_cost")
    timeout = int(getenv("RQ_JOB_TIMEOUT", "120"))
    result_ttl = int(getenv("RQ_RESULT_TTL", "0"))
    failure_ttl = int(getenv("RQ_FAILURE_TTL", "86400"))
    retry_max = int(getenv("RQ_RETRY_MAX", "3"))
    intervals_raw = getenv("RQ_RETRY_INTERVALS", "10,30,90")
    intervals = tuple(int(x) for x in intervals_raw.split(",") if x.strip())
    return RQConfig(
        redis_url=url,
        queue_name=name,
        job_func_path=func,
        job_timeout_s=timeout,
        result_ttl_s=result_ttl,
        failure_ttl_s=failure_ttl,
        retry_max=retry_max,
        retry_intervals=intervals or (10, 30, 90),
    )


def _app_env() -> str:
    return getenv("APP_ENV", "dev").lower()


def _validate_redis_url(url: str) -> None:
    if _app_env() != "prod":
        return
    parsed = urlparse(url)
    if parsed.scheme != "rediss":
        raise RuntimeError("Redis URL must use rediss:// when APP_ENV=prod")
    if not parsed.hostname:
        raise RuntimeError("Redis URL must include a host when APP_ENV=prod")


class RQQueue(CostEstimationQueue):
    """RQ-backed implementation of CostEstimationQueue."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        queue_name: Optional[str] = None,
        job_func_path: Optional[str] = None,
        job_timeout_s: Optional[int] = None,
        result_ttl_s: Optional[int] = None,
        failure_ttl_s: Optional[int] = None,
        retry_max: Optional[int] = None,
        retry_intervals: Optional[tuple[int, ...]] = None,
    ) -> None:
        cfg = _cfg_from_env()
        self._cfg = RQConfig(
            redis_url=redis_url or cfg.redis_url,
            queue_name=queue_name or cfg.queue_name,
            job_func_path=job_func_path or cfg.job_func_path,
            job_timeout_s=cfg.job_timeout_s if job_timeout_s is None else job_timeout_s,
            result_ttl_s=cfg.result_ttl_s if result_ttl_s is None else result_ttl_s,
            failure_ttl_s=cfg.failure_ttl_s if failure_ttl_s is None else failure_ttl_s,
            retry_max=cfg.retry_max if retry_max is None else retry_max,
            retry_intervals=cfg.retry_intervals if retry_intervals is None else retry_intervals,
        )
        _validate_redis_url(self._cfg.redis_url)
        self._redis = Redis.from_url(self._cfg.redis_url)
        self._q = Queue(
            name=self._cfg.queue_name,
            connection=self._redis,
            serializer=JSONSerializer,
        )

    def enqueue(self, request: CostRequestRecord) -> str:
        """Enqueue a job by request id. Worker will load data from Postgres."""
        req_id = str(request.id)
        desc = f"cost {request.ticker} {request.side} {request.shares} @ {request.d.isoformat()}"
        job = self._q.enqueue(
            self._cfg.job_func_path,  # dotted path; worker imports the callable
            args=(req_id,),  # only pass the id; state is persisted
            job_id=req_id,  # idempotent: one job per request
            description=desc,
            timeout=self._cfg.job_timeout_s,
            result_ttl=self._cfg.result_ttl_s,
            failure_ttl=self._cfg.failure_ttl_s,
            retry=Retry(max=self._cfg.retry_max, interval=list(self._cfg.retry_intervals)),
            meta={"request_id": req_id, "ticker": request.ticker, "side": str(request.side)},
        )
        return job.id


def make_rq_queue_from_env() -> RQQueue:
    """Factory with env defaults."""
    return RQQueue()
