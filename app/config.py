"""Environment configuration. Production = ENVIRONMENT=production, or Render's automatic RENDER variable."""
import os

DEV_SECRET = "dev-only-secret-not-for-production"
PLACEHOLDER_ADMIN_PASSWORDS = {"change-me-now", "changeme", "password", "admin"}


class ConfigError(RuntimeError):
    pass


def is_production(env=None) -> bool:
    env = os.environ if env is None else env
    return (env.get("ENVIRONMENT") or "").strip().lower() == "production" or bool((env.get("RENDER") or "").strip())


def database_url(env=None) -> str:
    env = os.environ if env is None else env
    raw = (env.get("DATABASE_URL") or "").strip()
    if not raw:
        if is_production(env):
            raise ConfigError("DATABASE_URL is required in production (use your PostgreSQL connection string).")
        return "sqlite:///wastemaster.db"  # local development default (also used when the variable is blank)
    for prefix in ("postgres://", "postgresql://"):  # Render gives postgres://; pin the driver we ship (psycopg2)
        if raw.startswith(prefix):
            return "postgresql+psycopg2://" + raw[len(prefix):]
    return raw


def secret_key(env=None) -> str:
    env = os.environ if env is None else env
    key = (env.get("SECRET_KEY") or "").strip()
    if is_production(env):
        if len(key) < 32 or key == DEV_SECRET or key.startswith("local-dev"):
            raise ConfigError("SECRET_KEY is missing, too short or a development value. In production set a random "
                              "value of 32+ characters, e.g. python -c \"import secrets; print(secrets.token_hex(32))\"")
        return key
    return key or DEV_SECRET


def cookie_secure(env=None) -> bool:
    env = os.environ if env is None else env
    flag = (env.get("COOKIE_SECURE") or "").strip()
    return flag == "1" if flag else is_production(env)  # HTTPS-only cookies by default in production
