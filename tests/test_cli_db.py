from __future__ import annotations
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from cost_estimator.cli import db as cli_db


def test_db_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert cli_db._db_url() == cli_db.DEFAULT_DB_URL

    monkeypatch.setenv("DATABASE_URL", "postgresql://example/testdb")
    assert cli_db._db_url() == "postgresql://example/testdb"


def test_alembic_cfg_uses_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    ini_path = tmp_path / "custom.ini"
    ini_path.write_text("[alembic]\n")
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()

    monkeypatch.setenv("DATABASE_URL", "postgresql://example/envdb")
    monkeypatch.setenv("ALEMBIC_INI", str(ini_path))
    monkeypatch.setenv("ALEMBIC_SCRIPT_LOCATION", str(script_dir))

    cfg = cli_db._alembic_cfg()

    assert cfg.get_main_option("sqlalchemy.url") == "postgresql://example/envdb"
    assert cfg.get_main_option("script_location") == str(script_dir)


def test_alembic_cfg_missing_script_location(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    ini_path = tmp_path / "custom.ini"
    ini_path.write_text("[alembic]\n")
    missing_dir = tmp_path / "missing"

    monkeypatch.setenv("ALEMBIC_INI", str(ini_path))
    monkeypatch.setenv("ALEMBIC_SCRIPT_LOCATION", str(missing_dir))

    with pytest.raises(SystemExit) as excinfo:
        cli_db._alembic_cfg()

    assert "script_location not found" in str(excinfo.value)


def test_seeds_path_returns_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    seeds_file = tmp_path / "seeds.sql"
    monkeypatch.setenv("SEEDS_FILE", str(seeds_file))
    assert cli_db._seeds_path() == seeds_file


def test_upgrade_head_invokes_alembic(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    cfg = SimpleNamespace()
    calls: list[tuple[object, str]] = []

    monkeypatch.setattr(cli_db, "_alembic_cfg", lambda: cfg)
    monkeypatch.setattr(cli_db.command, "upgrade", lambda cfg_arg, target: calls.append((cfg_arg, target)))

    cli_db.upgrade_head()

    assert calls == [(cfg, "head")]
    output = capsys.readouterr().out
    assert "migrated: head" in output


def test_seed_executes_sql_from_file(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    seeds_file = tmp_path / "seeds.sql"
    seeds_file.write_text("SELECT 1;")

    class _FakeEngine:
        def __init__(self) -> None:
            self.begin_calls = 0
            self.sql: list[str] = []

        @contextmanager
        def begin(self):
            self.begin_calls += 1
            conn = SimpleNamespace(exec_driver_sql=lambda sql: self.sql.append(sql))
            yield conn

    engine = _FakeEngine()

    monkeypatch.setattr(cli_db, "_seeds_path", lambda: seeds_file)
    monkeypatch.setattr(cli_db, "create_engine", lambda url, future: engine)
    monkeypatch.setattr(cli_db, "_db_url", lambda: "postgresql://example/seed")

    cli_db.seed()

    assert engine.begin_calls == 1
    assert engine.sql == ["SELECT 1;"]
    output = capsys.readouterr().out
    assert f"seeded: {seeds_file}" in output


def test_seed_missing_file_raises(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    missing = tmp_path / "none.sql"

    monkeypatch.setattr(cli_db, "_seeds_path", lambda: missing)

    with pytest.raises(SystemExit) as excinfo:
        cli_db.seed()

    assert str(missing) in str(excinfo.value)


def test_reset_runs_full_cycle(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    cfg = SimpleNamespace()
    calls: list[tuple[str, object, str]] = []
    seed_calls: list[str] = []

    monkeypatch.setattr(cli_db, "_alembic_cfg", lambda: cfg)
    monkeypatch.setattr(
        cli_db.command,
        "downgrade",
        lambda cfg_arg, target: calls.append(("downgrade", cfg_arg, target)),
    )
    monkeypatch.setattr(
        cli_db.command,
        "upgrade",
        lambda cfg_arg, target: calls.append(("upgrade", cfg_arg, target)),
    )
    monkeypatch.setattr(cli_db, "seed", lambda: seed_calls.append("seed"))

    cli_db.reset()

    assert calls == [("downgrade", cfg, "base"), ("upgrade", cfg, "head")]
    assert seed_calls == ["seed"]
    output = capsys.readouterr().out
    assert "reset complete" in output
