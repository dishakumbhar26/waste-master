"""Single source of truth for the request lifecycle and who may move it."""
from datetime import datetime, timezone
from app.models import StatusLog
from app.services.notification_service import notify, audit

NEXT = {"Open": "Scheduled", "Scheduled": "Picked up", "Picked up": "Completed"}


class WorkflowError(Exception):
    pass


def transition(s, q, actor):
    nxt = NEXT.get(q.status)
    if not nxt:
        raise WorkflowError("This request is already completed.")
    if nxt == "Scheduled":
        if actor.role != "provider" or not actor.approved:
            raise WorkflowError("Only approved providers can accept jobs.")
        q.provider_id = actor.id
    elif not (actor.role == "admin" or (actor.role == "provider" and q.provider_id == actor.id)):
        raise WorkflowError("You are not authorized to perform this action.")
    q.status = nxt
    if nxt == "Completed":
        q.completed_at = datetime.now(timezone.utc)
    s.add(q)
    s.add(StatusLog(request_id=q.id, status=nxt, by=actor.username))
    audit(s, actor, f"marked {nxt}" if nxt != "Scheduled" else "accepted request", "request", q.id)
    notify(s, q.builder_id, f"Your pickup #{q.id} is now {nxt}.", "request", f"req{q.id}:{nxt}")
    if nxt == "Scheduled":
        notify(s, actor.id, f"You accepted request #{q.id}.", "job", f"acc{q.id}")
    s.commit()
