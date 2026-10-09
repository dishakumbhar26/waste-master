import os, hashlib, secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from fastapi import HTTPException, Depends
from starlette.exceptions import HTTPException as SHTTP
from sqlalchemy import func
from sqlmodel import SQLModel, Session, create_engine, select
from app.models import *  # noqa
from app.services.ai_classifier import Classification
from app.services.impact_calculator import estimate, ensure_factors
from app.services.notification_service import notify, notify_role, audit
from app.services.workflow import transition, WorkflowError

from dotenv import load_dotenv
load_dotenv()  # read .env (real environment variables still win)
from app.db_migrate import migrate
from app.config import database_url, secret_key, cookie_secure, is_production
from app.bootstrap import ensure_admin
from app.security import hp, check

BASE = os.path.dirname(__file__)
SECRET = secret_key()  # fail fast on missing production secrets, before touching the database
DB = database_url()
engine = create_engine(DB, pool_pre_ping=True)  # pre_ping survives Postgres dropping idle connections
STATUSES = ["Open", "Scheduled", "Picked up", "Completed"]
# Rough illustrative estimates: tonnes CO2e avoided per tonne diverted/recycled.
CO2 = {"Concrete": 0.02, "Metal": 1.5, "Wood": 0.8, "Brick": 0.05, "Glass": 0.3, "Plastic": 1.0, "Other": 0.1, "Mixed Construction Waste": 0.1}


@asynccontextmanager
async def lifespan(app):
    migrate(DB)  # Alembic: creates a fresh DB or upgrades an existing one in place
    with Session(engine) as s:
        ensure_factors(s)
        ensure_admin(s, is_production())  # raises a clear error if production has no admin configured
    yield


async def csrf(request: Request):
    """Reject any POST whose form token doesn't match the session token."""
    if request.method == "POST":
        form = await request.form()
        if not secrets.compare_digest(str(form.get("csrf_token", "")), request.session.get("csrf", "-")):
            raise HTTPException(403, "Your session expired or the form was invalid. Please go back and try again.")


app = FastAPI(title="WasteMaster", lifespan=lifespan, dependencies=[Depends(csrf)])
app.add_middleware(SessionMiddleware, secret_key=SECRET, same_site="lax", max_age=8 * 3600, https_only=cookie_secure())
app.mount("/static", StaticFiles(directory=f"{BASE}/static"), name="static")
T = Jinja2Templates(directory=f"{BASE}/templates")


def page(r: Request, name: str, status: int = 200, **ctx):
    tok = r.session.setdefault("csrf", secrets.token_hex(16))
    flash = r.session.pop("flash", None)
    with Session(engine) as s:
        uid = r.session.get("uid")
        u = s.get(User, uid) if uid else None
        unread = s.exec(select(func.count()).select_from(Notification).where(Notification.user_id == uid, Notification.is_read == False)).one() if uid else 0  # noqa: E712
    return T.TemplateResponse(r, name, {"user": u, "csrf_token": tok, "unread": unread, "flash": flash, **ctx}, status_code=status)


def current(r: Request, s: Session, *roles):
    uid = r.session.get("uid")
    u = s.get(User, uid) if uid else None
    return u if u and (not roles or u.role in roles) else None


@app.exception_handler(SHTTP)
async def http_err(r: Request, e: SHTTP):
    msg = "Page not found." if e.status_code == 404 else (e.detail if e.status_code in (401, 403) else "Something went wrong.")
    return page(r, "error.html", status=e.status_code, msg=msg)


@app.exception_handler(Exception)
async def any_err(r: Request, e: Exception):
    return page(r, "error.html", status=500, msg="Something went wrong. Please try again.")


def go(url):
    return RedirectResponse(url, status_code=303)


def totals(s: Session):
    rows = s.exec(select(WasteRequest.waste_type, func.sum(WasteRequest.tons), func.count()).where(WasteRequest.status == "Completed").group_by(WasteRequest.waste_type)).all()
    by = {m: float(t) for m, t, _ in rows}
    co2, verified = estimate(s, by)
    return {"tons": round(sum(by.values()), 1), "co2": co2, "jobs": sum(c for *_, c in rows), "by": by, "verified": verified}


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}


@app.get("/")
def index(r: Request):
    with Session(engine) as s:
        return page(r, "index.html", t=totals(s))


@app.get("/about")
def about(r: Request):
    return page(r, "about.html")


@app.get("/register")
def register_form(r: Request):
    return page(r, "auth.html", mode="register", error=None)


@app.post("/register")
def register(r: Request, username: str = Form(...), email: str = Form(...), password: str = Form(...), role: str = Form(...)):
    if role not in ("builder", "provider") or len(password) < 6:
        return page(r, "auth.html", mode="register", error="Pick Builder or Provider and use a password of 6+ characters.")
    with Session(engine) as s:
        if s.exec(select(User).where(User.email == email.lower())).first():
            return page(r, "auth.html", mode="register", error="That email is already registered.")
        nu = User(username=username, email=email.lower(), password=hp(password), role=role, approved=(role == "builder"))
        s.add(nu); s.commit(); s.refresh(nu)
        audit(s, nu, "registered", "user", nu.id, f"role={role}")
        if role == "provider":
            notify_role(s, "admin", f"New provider registration requires approval: {username}", "admin", f"reg{nu.id}")
        s.commit()
    return go("/login")


@app.get("/login")
def login_form(r: Request):
    return page(r, "auth.html", mode="login", error=None)


@app.post("/login")
def login(r: Request, email: str = Form(...), password: str = Form(...)):
    with Session(engine) as s:
        u = s.exec(select(User).where(User.email == email.lower())).first()
        if not u or not check(password, u.password):
            return page(r, "auth.html", mode="login", error="Email or password is wrong.")
        r.session.clear()  # fresh session on login
        r.session["uid"] = u.id
    return go("/dashboard")


@app.post("/logout")
def logout(r: Request):
    r.session.clear()
    return go("/")


@app.get("/dashboard")
def dashboard(r: Request):
    with Session(engine) as s:
        u = current(r, s)
        if not u:
            return go("/login")
        if u.role == "admin":
            return go("/admin")
        if u.role == "builder":
            rows = s.exec(select(WasteRequest).where(WasteRequest.builder_id == u.id).order_by(WasteRequest.id.desc())).all()
            pids = {q.provider_id for q in rows if q.provider_id}
            names = {x.id: x.username for x in s.exec(select(User).where(User.id.in_(pids))).all()} if pids else {}
            return page(r, "dashboard.html", mine=rows, open=[], names=names)
        mine = s.exec(select(WasteRequest).where(WasteRequest.provider_id == u.id).order_by(WasteRequest.id.desc())).all()
        opn = s.exec(select(WasteRequest).where(WasteRequest.status == "Open")).all()
        return page(r, "dashboard.html", mine=mine, open=opn, statuses=STATUSES)


@app.get("/requests/new")
def new_form(r: Request):
    with Session(engine) as s:
        if not current(r, s, "builder"):
            return go("/login")
    return page(r, "new.html", types=list(CO2), error=None)


@app.post("/requests/new")
def new_request(r: Request, waste_type: str = Form(...), tons: float = Form(...), location: str = Form(...), pickup_date: str = Form(...), ai_json: str = Form("")):
    with Session(engine) as s:
        u = current(r, s, "builder")
        if not u:
            return go("/login")
        if waste_type not in CO2 or tons <= 0:
            return page(r, "new.html", types=list(CO2), error="Choose a waste type and enter a quantity above zero.")
        q = WasteRequest(builder_id=u.id, waste_type=waste_type, tons=tons, location=location, pickup_date=pickup_date)
        s.add(q); s.commit(); s.refresh(q)
        q.tracking_id = f"WM-{datetime.now().year}-{q.id:06d}"
        s.add(q); s.add(StatusLog(request_id=q.id, status="Open", by=u.username))
        audit(s, u, "created request", "request", q.id, f"{waste_type} {tons}t")
        notify(s, u.id, f"Your pickup request #{q.id} was created.", "request", f"req{q.id}:created")
        for pv in s.exec(select(User).where(User.role == "provider", User.approved == True)).all():  # noqa: E712
            notify(s, pv.id, f"New {waste_type} pickup request available (#{q.id}).", "job", f"job{q.id}:{pv.id}")
        if ai_json:
            try:  # never trust the client: re-validate. A bad AI payload must not block the request.
                c = Classification.model_validate_json(ai_json)
                s.add(WasteClassification(request_id=q.id, **c.model_dump()))
                audit(s, u, "AI classification saved", "request", q.id, c.material)
            except Exception:
                pass
        s.commit()
    return go("/dashboard")


@app.get("/requests/{rid}")
def detail(r: Request, rid: int):
    with Session(engine) as s:
        u = current(r, s)
        q = s.get(WasteRequest, rid)
        if not u or not q or u.id not in (q.builder_id, q.provider_id) and u.role != "admin":
            return go("/dashboard")
        logs = s.exec(select(StatusLog).where(StatusLog.request_id == rid).order_by(StatusLog.id)).all()
        ids = [i for i in (q.builder_id, q.provider_id) if i]
        nm = {x.id: x.username for x in s.exec(select(User).where(User.id.in_(ids))).all()}
        return page(r, "detail.html", q=q, logs=logs, bname=nm.get(q.builder_id), pname=nm.get(q.provider_id))


def _move(r, rid, roles):
    with Session(engine) as s:
        u = current(r, s, *roles)
        q = s.get(WasteRequest, rid)
        if not u:
            raise HTTPException(403, "You are not authorized to perform this action.")
        if not q:
            raise HTTPException(404)
        try:
            transition(s, q, u)
        except WorkflowError as e:
            raise HTTPException(403, str(e))
    return go("/dashboard")


@app.post("/requests/{rid}/accept")
def accept(r: Request, rid: int):
    return _move(r, rid, ("provider",))


@app.post("/requests/{rid}/advance")
def advance(r: Request, rid: int):
    return _move(r, rid, ("provider", "admin"))


@app.get("/impact")
def impact(r: Request):
    with Session(engine) as s:
        t = totals(s)
    return page(r, "impact.html", t=t, co2=CO2)


@app.get("/exchange")
def exchange(r: Request):
    with Session(engine) as s:
        rows = s.exec(select(Listing).order_by(Listing.id.desc())).all()
    return page(r, "exchange.html", rows=rows)


@app.post("/exchange")
def exchange_add(r: Request, material: str = Form(...), quantity: str = Form(...), location: str = Form(...), note: str = Form("")):
    with Session(engine) as s:
        u = current(r, s, "builder", "provider")
        if not u:
            return go("/login")
        s.add(Listing(owner_id=u.id, material=material, quantity=quantity, location=location, note=note)); s.commit()
    return go("/exchange")


@app.get("/admin")
def admin(r: Request):
    with Session(engine) as s:
        if not current(r, s, "admin"):
            return go("/login")
        pend = s.exec(select(User).where(User.role == "provider", User.approved == False)).all()  # noqa: E712
        reqs = s.exec(select(WasteRequest).order_by(WasteRequest.id.desc())).all()
        return page(r, "admin.html", pend=pend, reqs=reqs, t=totals(s))


@app.post("/admin/approve/{uid}")
def approve(r: Request, uid: int):
    with Session(engine) as s:
        ad = current(r, s, "admin")
        if not ad:
            raise HTTPException(403, "You are not authorized to perform this action.")
        u = s.get(User, uid)
        if u and not u.approved:
            u.approved = True; s.add(u)
            audit(s, ad, "approved provider", "user", u.id, u.username)
            notify(s, u.id, "Your provider account was approved. You can now accept jobs.", "admin", f"approved{u.id}")
            s.commit()
    return go("/admin")


from app.extras import router  # noqa: E402
app.include_router(router)
