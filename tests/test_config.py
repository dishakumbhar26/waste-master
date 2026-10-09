import os, subprocess, sys, pathlib
import pytest
from sqlmodel import Session, create_engine, select
from app import config
from app.bootstrap import ensure_admin
from app.config import ConfigError
from app.db_migrate import migrate
from app.models import AuditLog, User
from app.security import check

ROOT = pathlib.Path(__file__).resolve().parent.parent
GOOD = {"ENVIRONMENT": "production", "SECRET_KEY": "k" * 40, "DATABASE_URL": "postgres://u:p@host:5432/db"}


def test_blank_or_unset_database_url_falls_back_to_sqlite_in_dev():
    for env in ({}, {"DATABASE_URL": ""}, {"DATABASE_URL": "   "}):
        assert config.database_url(env) == "sqlite:///wastemaster.db"


def test_postgres_url_is_used_and_normalised():
    assert config.database_url(GOOD) == "postgresql+psycopg2://u:p@host:5432/db"
    assert config.database_url({"DATABASE_URL": "postgresql://u:p@h/d"}) == "postgresql+psycopg2://u:p@h/d"
    assert config.database_url({"DATABASE_URL": "postgresql+psycopg://u:p@h/d"}) == "postgresql+psycopg://u:p@h/d"


def test_production_requires_a_database_url():
    for blank in ("", "  "):
        with pytest.raises(ConfigError, match="DATABASE_URL"):
            config.database_url({**GOOD, "DATABASE_URL": blank})


@pytest.mark.parametrize("bad", [None, "", "   ", "short", config.DEV_SECRET, "local-dev-" + "x" * 40])
def test_production_rejects_missing_or_weak_secret(bad):
    env = {**GOOD, "SECRET_KEY": bad} if bad is not None else {k: v for k, v in GOOD.items() if k != "SECRET_KEY"}
    with pytest.raises(ConfigError, match="SECRET_KEY"):
        config.secret_key(env)
    assert config.secret_key(GOOD) == "k" * 40


def test_render_variable_counts_as_production_and_dev_still_has_fallback():
    with pytest.raises(ConfigError):
        config.secret_key({"RENDER": "true", "SECRET_KEY": ""})
    assert config.secret_key({}) == config.DEV_SECRET
    assert config.cookie_secure(GOOD) is True and config.cookie_secure({}) is False and config.cookie_secure({"COOKIE_SECURE": "0", **GOOD}) is False


def _run(env_extra, code):
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "SECRET_KEY", "RENDER", "ENVIRONMENT", "ADMIN_EMAIL", "ADMIN_PASSWORD")}
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**env, **env_extra}, capture_output=True, text=True)


def test_app_refuses_to_start_in_production_without_secret():
    r = _run({"ENVIRONMENT": "production", "DATABASE_URL": "postgresql://u:p@localhost/x", "SECRET_KEY": ""}, "import app.main")
    assert r.returncode != 0 and "SECRET_KEY" in r.stderr


def test_app_imports_with_blank_database_url_in_dev():
    r = _run({"DATABASE_URL": ""}, "from app.main import DB; print(DB)")
    assert r.returncode == 0 and r.stdout.strip() == "sqlite:///wastemaster.db"


@pytest.fixture
def s(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/a.db"; migrate(url)
    for k in ("ADMIN_EMAIL", "ADMIN_PASSWORD", "ADMIN_RESET_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    with Session(create_engine(url)) as sess:
        yield sess


def creds(mp, email=" Boss@Example.com ", pw="longpassword1", reset=None):
    mp.setenv("ADMIN_EMAIL", email); mp.setenv("ADMIN_PASSWORD", pw)
    mp.setenv("ADMIN_RESET_PASSWORD", reset) if reset else mp.delenv("ADMIN_RESET_PASSWORD", raising=False)


def test_admin_created_from_env_lowercased_and_idempotent(s, monkeypatch):
    creds(monkeypatch); ensure_admin(s, False); ensure_admin(s, False)
    admins = s.exec(select(User).where(User.role == "admin")).all()
    assert len(admins) == 1 and admins[0].email == "boss@example.com" and check("longpassword1", admins[0].password) and admins[0].approved


def test_existing_admin_password_only_reset_with_explicit_flag(s, monkeypatch):
    creds(monkeypatch); ensure_admin(s, False)
    creds(monkeypatch, pw="newpassword22"); ensure_admin(s, False)
    u = s.exec(select(User).where(User.role == "admin")).one(); assert check("longpassword1", u.password)       # unchanged
    creds(monkeypatch, pw="newpassword22", reset="1"); ensure_admin(s, False); s.refresh(u)
    assert check("newpassword22", u.password)
    assert "reset" in " ".join(a.action for a in s.exec(select(AuditLog)).all())


def test_non_admin_with_same_email_is_not_promoted(s, monkeypatch):
    s.add(User(username="b", email="boss@example.com", password="x$y", role="builder")); s.commit()
    creds(monkeypatch); ensure_admin(s, False)
    assert s.exec(select(User).where(User.email == "boss@example.com")).one().role == "builder"


def test_production_needs_admin_credentials_only_when_no_admin_exists(s, monkeypatch):
    with pytest.raises(ConfigError, match="ADMIN_EMAIL"):
        ensure_admin(s, True)
    creds(monkeypatch); ensure_admin(s, True)
    monkeypatch.delenv("ADMIN_EMAIL"); monkeypatch.delenv("ADMIN_PASSWORD")
    ensure_admin(s, True)  # redeploy with an admin already in the DB: fine


@pytest.mark.parametrize("pw", ["short", "changeme"])
def test_weak_admin_password_rejected(s, monkeypatch, pw):
    creds(monkeypatch, pw=pw)
    with pytest.raises(ConfigError, match="ADMIN_PASSWORD"):
        ensure_admin(s, True)
