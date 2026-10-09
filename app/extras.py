"""Routes for notifications, tracking/QR, manifests, AI, analytics and audit logs."""
from typing import Optional
from fastapi import APIRouter, Request, Form, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, desc
from sqlmodel import Session, select
from app.main import page, current, go, engine, CO2
from app.models import *  # noqa
from app.services import ai_classifier, qr_service, manifest_service
from app.services.impact_calculator import estimate
from app.services.notification_service import audit

router = APIRouter()
DENIED = "You are not authorized to perform this action."


@router.get("/notifications", tags=["notifications"])
def notifications(r: Request):
    with Session(engine) as s:
        u = current(r, s)
        if not u:
            return go("/login")
        rows = s.exec(select(Notification).where(Notification.user_id == u.id).order_by(desc(Notification.id)).limit(100)).all()
    return page(r, "notifications.html", rows=rows)


@router.post("/notifications/read", tags=["notifications"])
def mark_read(r: Request, nid: Optional[int] = Form(None)):
    with Session(engine) as s:
        u = current(r, s)
        if not u:
            raise HTTPException(403, DENIED)
        q = select(Notification).where(Notification.user_id == u.id, Notification.is_read == False)  # noqa: E712
        if nid:
            q = q.where(Notification.id == nid)
        for n in s.exec(q).all():
            n.is_read = True; s.add(n)
        s.commit()
    r.session["flash"] = "Notifications marked as read."
    return go("/notifications")


def _by_tracking(s, tid):
    q = s.exec(select(WasteRequest).where(WasteRequest.tracking_id == tid)).first()
    if not q:
        raise HTTPException(404, "Tracking ID not found.")
    return q


@router.get("/track/{tid}", tags=["tracking"])
def track(r: Request, tid: str):
    with Session(engine) as s:
        q = _by_tracking(s, tid)
        prov = s.get(User, q.provider_id).username if q.provider_id else None
    return page(r, "track.html", q=q, prov=prov)  # no builder or contact details exposed


@router.get("/qr/{tid}.png", tags=["tracking"])
def qr(r: Request, tid: str):
    with Session(engine) as s:
        _by_tracking(s, tid)
    return Response(qr_service.qr_png(f"{str(r.base_url).rstrip('/')}/track/{tid}"), media_type="image/png")


@router.get("/requests/{rid}/manifest.pdf", tags=["manifests"])
def manifest(r: Request, rid: int):
    with Session(engine) as s:
        u, q = current(r, s), s.get(WasteRequest, rid)
        if not u or not q or (u.role != "admin" and u.id not in (q.builder_id, q.provider_id)):
            raise HTTPException(403, DENIED)
        if q.status != "Completed":
            raise HTTPException(403, "A manifest is only available for completed requests.")
        try:
            nm = {x.id: x.username for x in s.exec(select(User).where(User.id.in_([q.builder_id, q.provider_id]))).all()}
            co2, ok = estimate(s, {q.waste_type: q.tons})
            ai = s.exec(select(WasteClassification).where(WasteClassification.request_id == rid)).first()
            rows = [("Request ID", f"#{q.id}"), ("Tracking ID", q.tracking_id), ("Builder", nm.get(q.builder_id)), ("Provider", nm.get(q.provider_id)),
                    ("Material", q.waste_type), ("Quantity", f"{q.tons} tonnes"), ("Pickup location", q.location), ("Pickup date", q.pickup_date),
                    ("Completed", f"{q.completed_at:%d %b %Y}" if q.completed_at else "-"), ("Status", q.status),
                    ("Est. CO2e avoided", f"{co2} t" + ("" if ok else " (methodology under review)")),
                    ("AI classification", (f"Used: {ai.material}" + (f" ({ai.confidence:.0%} model-reported confidence)" if ai.confidence is not None else "")) if ai else "Not used")]
            pdf = manifest_service.build_pdf(f"MF-{q.tracking_id}", rows)
        except Exception:
            raise HTTPException(500, "Unable to generate the manifest.")
        fname = f"manifest-{q.tracking_id}.pdf"
        audit(s, u, "generated manifest", "request", rid); s.commit()
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/ai/classify", tags=["ai"])
async def ai_classify(r: Request, image: UploadFile = File(...)):
    with Session(engine) as s:
        if not current(r, s):
            raise HTTPException(403, DENIED)
    if image.content_type not in ("image/jpeg", "image/png", "image/webp"):
        return JSONResponse({"ok": False, "message": "Please upload a JPEG, PNG or WebP image."})
    data = await image.read(5_000_001)  # image is sent to the API, never written to disk
    if len(data) > 5_000_000:
        return JSONResponse({"ok": False, "message": "Image is too large (5 MB max)."})
    c, err = ai_classifier.classify(data, image.content_type)
    return JSONResponse({"ok": True, "result": c.model_dump()} if c else {"ok": False, "message": err})


@router.get("/analytics", tags=["analytics"])
def analytics(r: Request, material: str = "", status: str = "", date_from: str = "", date_to: str = ""):
    W = WasteRequest
    with Session(engine) as s:
        if not current(r, s):
            return go("/login")

        def f(q):
            for cond, clause in ((material, W.waste_type == material), (status, W.status == status), (date_from, W.pickup_date >= date_from), (date_to, W.pickup_date <= date_to)):
                if cond:
                    q = q.where(clause)
            return q
        done = W.status == "Completed"
        rows = lambda q: [(a, float(b)) for a, b in s.exec(q).all()]  # noqa: E731
        by = rows(f(select(W.waste_type, func.sum(W.tons)).where(done).group_by(W.waste_type)))
        over = rows(f(select(func.substr(W.pickup_date, 1, 7), func.sum(W.tons)).where(done).group_by(func.substr(W.pickup_date, 1, 7)).order_by(func.substr(W.pickup_date, 1, 7))))
        stat = rows(f(select(W.status, func.count()).group_by(W.status)))
        prov = rows(f(select(User.username, func.sum(W.tons)).join(W, W.provider_id == User.id).where(done).group_by(User.username)))
        total = s.exec(f(select(func.count()).select_from(W))).one()
        active = s.exec(f(select(func.count()).select_from(W)).where(W.status != "Completed")).one()
        approved = s.exec(select(func.count()).select_from(User).where(User.role == "provider", User.approved == True)).one()  # noqa: E712
        co2, ok = estimate(s, dict(by))
    kpi = {"tons": round(sum(v for _, v in by), 1), "co2": co2, "verified": ok, "jobs": sum(1 for _ in by) and s_count(by, stat), "active": active, "providers": approved, "total": total}
    return page(r, "analytics.html", by=by, over=over, stat=stat, prov=prov, kpi=kpi, materials=list(CO2), f=dict(material=material, status=status, date_from=date_from, date_to=date_to))


def s_count(by, stat):
    return int(dict(stat).get("Completed", 0))


@router.get("/admin/audit", tags=["admin"])
def audit_log(r: Request):
    with Session(engine) as s:
        if not current(r, s, "admin"):
            raise HTTPException(403, DENIED)
        rows = s.exec(select(AuditLog).order_by(desc(AuditLog.timestamp), desc(AuditLog.id)).limit(200)).all()
    return page(r, "audit.html", rows=rows)
