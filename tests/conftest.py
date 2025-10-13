from __future__ import annotations

import sys
import types
from types import SimpleNamespace


def _ensure_fastapi_stub() -> None:
    if "fastapi" in sys.modules:
        return

    fastapi_module = types.ModuleType("fastapi")

    class FastAPI:
        def __init__(self, *args, **kwargs) -> None:
            self.state = SimpleNamespace()

        def get(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

        def post(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    def Depends(dep):
        return dep

    def Query(*args, **kwargs):
        return kwargs.get("default") if kwargs else (args[0] if args else None)

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str | None = None) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class Request:
        def __init__(self) -> None:
            self.app = SimpleNamespace(state=SimpleNamespace(deps=None))

    status = SimpleNamespace(HTTP_404_NOT_FOUND=404)
    encoders_module = types.ModuleType("fastapi.encoders")

    def jsonable_encoder(obj, *args, **kwargs):
        return obj

    encoders_module.jsonable_encoder = jsonable_encoder

    fastapi_module.encoders = encoders_module

    fastapi_module.FastAPI = FastAPI
    fastapi_module.Depends = Depends
    fastapi_module.Query = Query
    fastapi_module.HTTPException = HTTPException
    fastapi_module.Request = Request
    fastapi_module.status = status

    sys.modules["fastapi"] = fastapi_module
    sys.modules["fastapi.encoders"] = encoders_module


def _ensure_redis_stub() -> None:
    if "redis" in sys.modules:
        return

    redis_module = types.ModuleType("redis")

    class Redis:
        def __init__(self) -> None:
            self.store: dict[str, object] = {}

        @classmethod
        def from_url(cls, url: str, decode_responses: bool = True):
            return cls()

        def get(self, key: str):
            return self.store.get(key)

        def set(self, key: str, value: object) -> None:
            self.store[key] = value

        def setex(self, key: str, ttl: int, value: object) -> None:
            self.store[key] = value

        def close(self) -> None:
            return None

    redis_module.Redis = Redis
    sys.modules["redis"] = redis_module


def _ensure_alembic_stub() -> None:
    if "alembic" in sys.modules:
        return

    alembic_module = types.ModuleType("alembic")
    command_module = types.ModuleType("alembic.command")
    config_module = types.ModuleType("alembic.config")

    class Config:
        def __init__(self, ini_path: str) -> None:
            self.ini_path = ini_path
            self.options: dict[str, str] = {}

        def set_main_option(self, key: str, value: str) -> None:
            self.options[key] = value

        def get_main_option(self, key: str) -> str | None:
            return self.options.get(key)

    def upgrade(cfg, target):
        return None

    def downgrade(cfg, target):
        return None

    config_module.Config = Config
    command_module.upgrade = upgrade
    command_module.downgrade = downgrade
    alembic_module.command = command_module
    alembic_module.config = config_module

    sys.modules["alembic"] = alembic_module
    sys.modules["alembic.command"] = command_module
    sys.modules["alembic.config"] = config_module


def _ensure_psycopg_stub() -> None:
    if "psycopg" in sys.modules:
        return

    psycopg_module = types.ModuleType("psycopg")

    class Connection:
        def __init__(self) -> None:
            self.row_factory = None

    def connect(*args, **kwargs):
        return Connection()

    psycopg_module.Connection = Connection
    psycopg_module.connect = connect

    rows_module = types.ModuleType("psycopg.rows")

    def dict_row(*args, **kwargs):
        return dict

    rows_module.dict_row = dict_row

    json_module = types.ModuleType("psycopg.types.json")

    class Json:
        def __init__(self, value) -> None:
            self.value = value

    json_module.Json = Json

    types_module = types.ModuleType("psycopg.types")
    types_module.json = json_module

    psycopg_pool_module = types.ModuleType("psycopg_pool")

    class ConnectionPool:
        def __init__(self, conninfo: str, open: bool = True) -> None:
            self.conninfo = conninfo

        class _Ctx:
            def __enter__(self_ctx):
                return Connection()

            def __exit__(self_ctx, exc_type, exc, tb) -> None:
                return None

        def connection(self) -> ConnectionPool._Ctx:
            return self._Ctx()

        def close(self) -> None:
            return None

    psycopg_pool_module.ConnectionPool = ConnectionPool

    sys.modules["psycopg"] = psycopg_module
    sys.modules["psycopg.rows"] = rows_module
    sys.modules["psycopg.types"] = types_module
    sys.modules["psycopg.types.json"] = json_module
    sys.modules["psycopg_pool"] = psycopg_pool_module


def _ensure_rq_stub() -> None:
    if "rq" in sys.modules:
        return

    rq_module = types.ModuleType("rq")

    class Retry:
        def __init__(self, max: int, interval):
            self.max = max
            self.interval = interval

    class Queue:
        def __init__(self, name, connection, serializer) -> None:
            self.name = name
            self.connection = connection
            self.serializer = serializer
            self.enqueued: list[SimpleNamespace] = []

        def enqueue(self, *args, job_id=None, **kwargs):
            job = SimpleNamespace(id=job_id or "job")
            self.enqueued.append(job)
            return job

    rq_module.Queue = Queue
    rq_module.Retry = Retry

    serializers_module = types.ModuleType("rq.serializers")
    serializers_module.JSONSerializer = object()

    sys.modules["rq"] = rq_module
    sys.modules["rq.serializers"] = serializers_module


_ensure_fastapi_stub()
_ensure_redis_stub()
_ensure_alembic_stub()
_ensure_psycopg_stub()
_ensure_rq_stub()
