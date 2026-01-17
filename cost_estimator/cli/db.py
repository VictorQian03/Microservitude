from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_DB_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/costdb"
DEFAULT_ALEMBIC_INI = REPO_ROOT / "db" / "alembic.ini"
DEFAULT_SCRIPT_LOCATION = REPO_ROOT / "db" / "migrations"
DEFAULT_SEEDS = REPO_ROOT / "db" / "seeds.sql"


def _app_env() -> str:
    return os.getenv("APP_ENV", "dev").lower()


def _db_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    if _app_env() == "prod":
        raise SystemExit("DATABASE_URL must be set when APP_ENV=prod")
    return DEFAULT_DB_URL


def _require_reset_confirmation() -> None:
    if _app_env() == "prod":
        raise SystemExit("ce-db-reset is blocked when APP_ENV=prod")
    confirm = os.getenv("CE_DB_RESET_CONFIRM", "").strip().lower()
    if confirm not in {"1", "true", "yes"}:
        raise SystemExit("Set CE_DB_RESET_CONFIRM=1 to allow ce-db-reset")


def _alembic_cfg() -> Config:
    ini_path = Path(os.getenv("ALEMBIC_INI", str(DEFAULT_ALEMBIC_INI))).resolve()
    script_loc = Path(os.getenv("ALEMBIC_SCRIPT_LOCATION", str(DEFAULT_SCRIPT_LOCATION))).resolve()
    if not script_loc.exists():
        raise SystemExit(f"alembic script_location not found: {script_loc}")
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", _db_url())
    cfg.set_main_option("script_location", str(script_loc))
    return cfg


def _seeds_path() -> Path:
    return Path(os.getenv("SEEDS_FILE", str(DEFAULT_SEEDS))).resolve()


def upgrade_head() -> None:
    cfg = _alembic_cfg()
    command.upgrade(cfg, "head")
    print("migrated: head")


def seed() -> None:
    seeds = _seeds_path()
    if not seeds.exists():
        raise SystemExit(f"seeds file not found: {seeds}")
    engine = create_engine(_db_url(), future=True)
    sql = seeds.read_text()
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)
    print(f"seeded: {seeds}")


def reset() -> None:
    _require_reset_confirmation()
    cfg = _alembic_cfg()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    seed()
    print("reset complete")
