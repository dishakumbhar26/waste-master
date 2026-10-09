import re
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services import ai_classifier


def tok(c):
    return re.search(r'csrf-token" content="(\w+)"', c.get("/about").text).group(1)


def post(c, path, **data):
    return c.post(path, data={**data, "csrf_token": tok(c)})


def user(c, name, role, approve_by=None):
    post(c, "/register", username=name, email=f"{name}@x.com", password="secret1", role=role)
    post(c, "/login", email=f"{name}@x.com", password="secret1")


@pytest.fixture(scope="module")
def world():
    with TestClient(app) as b, TestClient(app) as p, TestClient(app) as p2, TestClient(app) as a:
        user(b, "builder", "builder"); user(p, "prov", "provider"); user(p2, "prov2", "provider")
        post(a, "/login", email="a@x.com", password="adminpass1")
        yield dict(b=b, p=p, p2=p2, a=a)


def test_csrf_blocks_forged_post(world):
    assert world["b"].post("/requests/new", data=dict(waste_type="Metal", tons=1, location="x", pickup_date="2026-11-01")).status_code == 403


def test_unapproved_provider_cannot_accept_and_builder_not_admin(world):
    b, p = world["b"], world["p"]
    post(b, "/requests/new", waste_type="Metal", tons=5, location="Pune", pickup_date="2026-11-01")
    assert post(p, "/requests/1/accept").status_code == 403
    assert "Only approved providers" in post(p, "/requests/1/accept").text
    assert b.get("/admin", follow_redirects=False).status_code in (303, 307)
    assert b.get("/admin/audit").status_code == 403
    assert post(b, "/admin/approve/3").status_code == 403


def test_full_lifecycle_impact_notifications_audit_pdf_qr(world):
    a, b, p, p2 = world["a"], world["b"], world["p"], world["p2"]
    pid = [i for i in (3, 4) if True]
    post(a, "/admin/approve/3"); post(a, "/admin/approve/4")
    assert post(p, "/requests/1/accept").status_code == 200
    assert post(p2, "/requests/1/advance").status_code == 403          # another provider's job
    assert post(b, "/requests/1/advance").status_code == 403           # builder can't move status
    post(p, "/requests/1/advance"); post(p, "/requests/1/advance")
    assert "already completed" in post(p, "/requests/1/advance").text   # no Completed -> anything
    assert "5.0 t" in b.get("/impact").text and "5.0" in b.get("/analytics").text
    assert "Your pickup #1 is now Completed" in b.get("/notifications").text
    assert "approved provider" in a.get("/admin/audit").text and "marked Completed" in a.get("/admin/audit").text
    pdf = b.get("/requests/1/manifest.pdf"); assert pdf.content[:4] == b"%PDF"
    assert p2.get("/requests/1/manifest.pdf").status_code == 403
    tid = f"WM-{__import__('datetime').datetime.now().year}-000001"
    assert b.get(f"/qr/{tid}.png").content[:4] == b"\x89PNG"
    t = b.get(f"/track/{tid}").text; assert "Metal" in t and "builder@x.com" not in t
    assert b.get("/track/WM-0000-000000").status_code == 404


def test_no_duplicate_notifications(world):
    b = world["b"]
    n = b.get("/notifications").text.count("is now Scheduled")
    assert n == 1


def test_ai_validation_and_fallback(world):
    good = '{"material":"Concrete","confidence":0.9,"recyclable":true,"recommended_action":"Recycle","possible_reuse":"Aggregate"}'
    assert ai_classifier.parse("```json\n" + good + "\n```").material == "Concrete"
    for bad in ('{"material":"Wood","confidence":1.7,"recyclable":true,"recommended_action":"x"}', "nonsense"):
        with pytest.raises(Exception):
            ai_classifier.parse(bad)
    r = world["b"].post("/ai/classify", data={"csrf_token": tok(world["b"])}, files={"image": ("a.png", b"x", "image/png")})
    assert "ANTHROPIC_API_KEY" in r.json()["message"]
    # AI failure / garbage payload must not block request creation
    assert post(world["b"], "/requests/new", waste_type="Wood", tons=2, location="Pune", pickup_date="2026-12-01", ai_json="garbage").status_code == 200
    assert "Wood" in world["b"].get("/dashboard").text


def test_pages_and_docs(world):
    for u in ("/", "/about", "/impact", "/exchange", "/docs", "/redoc"):
        assert world["b"].get(u).status_code == 200
    assert world["b"].get("/nope").status_code == 404 and "Traceback" not in world["b"].get("/nope").text
