"""
sitecustomize hook that pre-imports optional dependencies when available.

The test suite ships lightweight stubs for FastAPI, Redis, Alembic, Psycopg,
and RQ so that unit tests can run even when those extras are missing. When the
real packages are installed (as they are in CI), we want pytest to use them
instead of the fallbacks. Importing the modules here ensures they are present
in ``sys.modules`` before the stubs run, which prevents the shim from
overriding the genuine implementations.
"""

from __future__ import annotations


def _try_import(module: str) -> None:
    try:
        __import__(module)
    except Exception:
        # Missing optional dependency; keep going so the stub can provide it.
        pass


for _module in (
    "fastapi",
    "redis",
    "alembic",
    "psycopg",
    "rq",
):
    _try_import(_module)

