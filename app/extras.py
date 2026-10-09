"""Routes for notifications, tracking/QR, manifests, AI, analytics and audit logs."""
import csv, io, re
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Request, Form, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, desc, case
from sqlmodel import Session, select
from app.main import page, current, go, engine, CO2
from app.models import *  # noqa
from app.services import qr_service, manifest_service
from app.services.photo_service import process_photo, MAX_UPLOAD
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
                    ("Est. CO2e avoided", f"{co2} t" + ("" if ok else " (methodology under review)"))]
            if ai:
                rows.append(("Earlier AI suggestion", f"{ai.material} (historical record)"))
            pdf = manifest_service.build_pdf(f"MF-{q.tracking_id}", rows)
        except Exception:
            raise HTTPException(500, "Unable to generate the manifest.")
        fname = f"manifest-{q.tracking_id}.pdf"
        audit(s, u, "generated manifest", "request", rid); s.commit()
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fname}"'})


def _participant(u, q):
    return bool(u and q and (u.role == "admin" or u.id in (q.builder_id, q.provider_id)))


@router.get("/requests/{rid}/photo", tags=["requests"])
def photo(r: Request, rid: int):
    with Session(engine) as s:
        u, q = current(r, s), s.get(WasteRequest, rid)
        if not _participant(u, q):
            raise HTTPException(403, DENIED)
        p = s.exec(select(RequestPhoto).where(RequestPhoto.request_id == rid)).first()
        if not p:
            raise HTTPException(404, "No photo for this request.")
        data, ctype = p.data, p.content_type
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.post("/requests/{rid}/photo", tags=["requests"])
def upload_photo(r: Request, rid: int, photo: UploadFile = File(...)):
    with Session(engine) as s:
        u, q = current(r, s, "builder"), s.get(WasteRequest, rid)
        if not u or not q or q.builder_id != u.id:
            raise HTTPException(403, DENIED)
        try:
            jpeg = process_photo(photo.file.read(MAX_UPLOAD + 1))
        except ValueError as e:
            r.session["flash"] = "err:" + str(e)
            return go(f"/requests/{rid}")
        p = s.exec(select(RequestPhoto).where(RequestPhoto.request_id == rid)).first()
        if p:
            p.data, p.size, p.uploaded_by, p.created_at = jpeg, len(jpeg), u.id, datetime.now(timezone.utc)
        else:
            p = RequestPhoto(request_id=rid, size=len(jpeg), data=jpeg, uploaded_by=u.id)
        s.add(p); audit(s, u, "uploaded photo", "request", rid); s.commit()
    r.session["flash"] = "Photo saved."
    return go(f"/requests/{rid}")


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
        mon = func.substr(W.pickup_date, 1, 7)
        mrows = s.exec(f(select(mon, func.count(), func.sum(case((W.status == "Completed", 1), else_=0)), func.sum(W.tons),
                                func.sum(case((W.status == "Completed", W.tons), else_=0))).group_by(mon).order_by(mon))).all()
        months = [dict(month=m, requests=int(n), completed=int(c or 0), requested=round(float(t or 0), 1), diverted=round(float(d or 0), 1),
                       rate=round(100 * (c or 0) / n) if n else 0) for m, n, c, t, d in mrows]
        tn, tc = sum(x["requests"] for x in months), sum(x["completed"] for x in months)
        tr, td = sum(x["requested"] for x in months), sum(x["diverted"] for x in months)
        stats = dict(requests=tn, completed=tc, completion_rate=round(100 * tc / tn) if tn else 0, requested=round(tr, 1), diverted=round(td, 1),
                     diversion_rate=round(100 * td / tr) if tr else 0, avg_job=round(td / tc, 1) if tc else 0, top=max(by, key=lambda x: x[1])[0] if by else None)
        co2, ok = estimate(s, dict(by))
    kpi = {"tons": round(sum(v for _, v in by), 1), "co2": co2, "verified": ok, "jobs": sum(1 for _ in by) and s_count(by, stat), "active": active, "providers": approved, "total": total}
    return page(r, "analytics.html", months=months, stats=stats, trend=[(m["month"], m["diverted"]) for m in months], by=by, over=over, stat=stat, prov=prov, kpi=kpi, materials=list(CO2), f=dict(material=material, status=status, date_from=date_from, date_to=date_to))


def s_count(by, stat):
    return int(dict(stat).get("Completed", 0))


@router.get("/analytics/report", tags=["analytics"])
def analytics_report(r: Request, month: str = "", fmt: str = "csv"):
    month = month or datetime.now().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) or fmt not in ("csv", "pdf"):
        raise HTTPException(400, "Choose a month like 2026-10 and a format of csv or pdf.")
    W = WasteRequest
    with Session(engine) as s:
        u = current(r, s)
        if not u:
            raise HTTPException(403, DENIED)
        sel = W.pickup_date.startswith(month)
        total, done = s.exec(select(func.count(), func.sum(case((W.status == "Completed", 1), else_=0))).where(sel)).one()
        done, req_t = int(done or 0), float(s.exec(select(func.sum(W.tons)).where(sel)).one() or 0)
        by = [(m, float(t), int(n)) for m, t, n in s.exec(select(W.waste_type, func.sum(W.tons), func.count()).where(sel, W.status == "Completed").group_by(W.waste_type).order_by(W.waste_type)).all()]
        co2, ok = estimate(s, {m: t for m, t, _ in by})
        audit(s, u, "downloaded monthly report", "report", None, f"{month} {fmt}"); s.commit()
    div_t = sum(t for _, t, _ in by)
    rows = [("Month (by pickup date)", month), ("Requests", total), ("Completed jobs", done), ("Completion rate", f"{round(100 * done / total) if total else 0}%"),
            ("Tonnes requested", f"{req_t:.1f}"), ("Tonnes diverted", f"{div_t:.1f}")]
    rows += [(f"  {m}", f"{t:.1f} t in {n} job(s)") for m, t, n in by]
    rows.append(("Est. CO2e avoided", f"{co2} t" + ("" if ok else " (methodology under review)")))
    if fmt == "pdf":
        body, mt = manifest_service.build_pdf(month, rows, title="Monthly Waste Report", heading="Report"), "application/pdf"
    else:
        buf = io.StringIO(); w = csv.writer(buf); w.writerow(["WasteMaster monthly report"]); w.writerows(rows); body, mt = buf.getvalue(), "text/csv"
    return Response(body, media_type=mt, headers={"Content-Disposition": f'attachment; filename="wastemaster-report-{month}.{fmt}"'})


@router.get("/admin/audit", tags=["admin"])
def audit_log(r: Request):
    with Session(engine) as s:
        if not current(r, s, "admin"):
            raise HTTPException(403, DENIED)
        rows = s.exec(select(AuditLog).order_by(desc(AuditLog.timestamp), desc(AuditLog.id)).limit(200)).all()
    return page(r, "audit.html", rows=rows)
