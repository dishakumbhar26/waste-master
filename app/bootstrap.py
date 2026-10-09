"""Admin account initialisation from ADMIN_EMAIL / ADMIN_PASSWORD. Never silently resets an existing account."""
import logging, os
from sqlmodel import select
from app.config import ConfigError, PLACEHOLDER_ADMIN_PASSWORDS
from app.models import User
from app.security import hp
from app.services.notification_service import audit

log = logging.getLogger("wastemaster")


def ensure_admin(s, production: bool):
    email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()  # emails are stored lowercase, like registration
    pw = os.getenv("ADMIN_PASSWORD") or ""
    reset = (os.getenv("ADMIN_RESET_PASSWORD") or "").strip() == "1"
    has_admin = s.exec(select(User.id).where(User.role == "admin")).first() is not None
    if not email or not pw:
        if production and not has_admin:
            raise ConfigError("No admin account exists. Set ADMIN_EMAIL and ADMIN_PASSWORD for the first deploy.")
        return
    if len(pw) < 8 or (production and pw.lower() in PLACEHOLDER_ADMIN_PASSWORDS):
        raise ConfigError("ADMIN_PASSWORD must be at least 8 characters and not a placeholder value.")
    u = s.exec(select(User).where(User.email == email)).first()
    if not u:
        u = User(username="admin", email=email, password=hp(pw), role="admin", approved=True)
        s.add(u); s.commit(); s.refresh(u)
        audit(s, u, "admin created from environment", "user", u.id); s.commit()
        log.info("Admin account created for %s", email)
    elif u.role != "admin":
        log.warning("ADMIN_EMAIL belongs to a non-admin user; not promoted. Use: python -m app.manage make-admin EMAIL")
    elif reset:
        u.password = hp(pw); s.add(u)
        audit(s, u, "admin password reset from environment", "user", u.id); s.commit()
        log.warning("Admin password reset via ADMIN_RESET_PASSWORD=1. Remove that variable now.")
    else:
        log.info("Admin %s already exists; password left unchanged.", email)
