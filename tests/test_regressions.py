import re, pathlib
from fastapi.testclient import TestClient
from app.main import app
from tests.test_flow import tok, post, user


def req(c):
    post(c, "/requests/new", waste_type="Concrete", tons=3, location="Pune", pickup_date="2026-11-01")


def form_tokens(html):
    return re.findall(r'action="/notifications/read"><input type="hidden" name="csrf_token" value="(\w+)"><input type="hidden" name="nid" value="(\d+)"', html)


def test_every_post_form_carries_a_csrf_token():
    for f in pathlib.Path("app/templates").glob("*.html"):
        t = f.read_text(encoding="utf-8")
        for m in re.finditer(r'<form[^>]*method="post"[^>]*>', t, re.I):
            assert "csrf_token" in t[m.end():m.end() + 90], f"{f.name}: POST form without CSRF token"


def test_notification_mark_read_regression():
    with TestClient(app) as b, TestClient(app) as o, TestClient(app) as anon:
        user(b, "nb", "builder"); user(o, "no", "builder"); req(b); req(o)
        page = b.get("/notifications").text
        (t, nid), = form_tokens(page)[:1]                       # token exactly as the browser would submit it
        assert b.post("/notifications/read", data={"csrf_token": "bad", "nid": nid}).status_code == 403
        assert anon.post("/notifications/read", data={"csrf_token": tok(anon)}).status_code == 403
        post(o, "/notifications/read", nid=nid)                  # another user's notification: must not change
        assert f'name="nid" value="{nid}"' in b.get("/notifications").text
        r = b.post("/notifications/read", data={"csrf_token": t, "nid": nid})
        assert r.status_code == 200 and "marked as read" in r.text and f'name="nid" value="{nid}"' not in r.text
        req(b); assert "pill" in b.get("/notifications").text
        post(b, "/notifications/read"); assert "pill" not in b.get("/notifications").text and "Mark all as read" not in b.get("/notifications").text


def test_logout_is_csrf_protected_post(world=None):
    with TestClient(app) as c:
        user(c, "lo", "builder")
        assert c.get("/logout").status_code == 405
        assert c.post("/logout", data={"csrf_token": "bad"}).status_code == 403
        post(c, "/logout"); assert "Log in" in c.get("/").text


def test_dotenv_is_loaded(tmp_path, monkeypatch):
    from dotenv import load_dotenv
    import os
    f = tmp_path / ".env"; f.write_text("WM_TEST_VAR=from-dotenv\n"); monkeypatch.delenv("WM_TEST_VAR", raising=False)
    load_dotenv(f); assert os.getenv("WM_TEST_VAR") == "from-dotenv"
    assert "load_dotenv()" in pathlib.Path("app/main.py").read_text()
