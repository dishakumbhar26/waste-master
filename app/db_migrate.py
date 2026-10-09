"""Run Alembic on startup. Existing pre-migration databases are stamped, never recreated."""
import os
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config(url: str) -> Config:
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def migrate(url: str):
    eng = create_engine(url); tables = inspect(eng).get_table_names(); eng.dispose()
    cfg = config(url)
    if "user" in tables and "alembic_version" not in tables:
        command.stamp(cfg, "0001")  # database created before migrations existed: adopt it as-is
    command.upgrade(cfg, "head")
