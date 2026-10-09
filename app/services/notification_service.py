from sqlmodel import select
from app.models import Notification, AuditLog, User


def notify(s, user_id, message, type_, key):
    """Add a notification unless one with the same dedupe key exists (caller commits)."""
    k = f"{user_id}:{key}"
    if not s.exec(select(Notification.id).where(Notification.dedupe_key == k)).first():
        s.add(Notification(user_id=user_id, message=message, type=type_, dedupe_key=k))


def notify_role(s, role, message, type_, key):
    for uid in s.exec(select(User.id).where(User.role == role)).all():
        notify(s, uid, message, type_, key)


def audit(s, user, action, entity, eid=None, detail=""):
    s.add(AuditLog(user_id=user.id if user else None, actor=user.username if user else "system", action=action, entity_type=entity, entity_id=eid, detail=detail))
