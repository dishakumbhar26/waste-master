import re, pathlib
import httpx, pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services import ai_classifier as ai
from tests.test_flow import tok, post, user


def req(c):
    post(c, "/requests/new", waste_type="Concrete", tons=3, location="Pune", pickup_date="2026-11-01")


def form_tokens(html):
    return re.findall(r'action="/notifications/read"><input type="hidden" name="csrf_token" value="(\w+)"><input type="hidden" name="nid" value="(\d+)"', html)


def test_every_post_form_carries_a_csrf_token():
    for f in pathlib.Path("app/templates").glob("*.html"):
        t = f.read_text()
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


class Resp:
    def __init__(self, code=200, text=""):
        self.code, self.text = code, text
    def raise_for_status(self):
        if self.code >= 400:
            raise httpx.HTTPStatusError("e", request=httpx.Request("POST", "http://x"), response=httpx.Response(self.code))
    def json(self):
        return {"content": [{"type": "text", "text": self.text}]}


GOOD = '{"material":"concrete","confidence":null,"recyclable":true,"recommended_action":"Recycle","possible_reuse":"Aggregate"}'


@pytest.mark.parametrize("resp,expect", [
    (Resp(200, GOOD), None), (Resp(200, "not json"), "could not be understood"), (Resp(200, '{"material":"Wood"}'), "could not be understood"),
    (Resp(401), "rejected the API key"), (Resp(500), "(500)"), (httpx.ReadTimeout("t"), "timed out")])
def test_classifier_with_mocked_api(monkeypatch, resp, expect):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    def fake(*a, **k):
        if isinstance(resp, Exception):
            raise resp
        return resp
    monkeypatch.setattr(ai.httpx, "post", fake)
    c, err = ai.classify(b"img", "image/png")
    if expect is None:
        assert c.material == "Concrete" and c.confidence is None and err is None   # case mapped, no fake confidence
    else:
        assert c is None and expect in err and "test-key" not in err


def test_ai_route_validates_upload_and_hides_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(ai.httpx, "post", lambda *a, **k: Resp(200, GOOD))
    with TestClient(app) as c:
        user(c, "aiu", "builder")
        up = lambda name, data, ct: c.post("/ai/classify", data={"csrf_token": tok(c)}, files={"image": (name, data, ct)}).json()
        assert "JPEG, PNG or WebP" in up("a.txt", b"x", "text/plain")["message"]
        assert "too large" in up("a.png", b"x" * 5_000_001, "image/png")["message"]
        ok = up("a.png", b"x", "image/png"); assert ok["ok"] and ok["result"]["material"] == "Concrete" and "test-key" not in str(ok)


def test_dotenv_is_loaded(tmp_path, monkeypatch):
    from dotenv import load_dotenv
    import os
    f = tmp_path / ".env"; f.write_text("ANTHROPIC_API_KEY=from-dotenv\n"); monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    load_dotenv(f); assert os.getenv("ANTHROPIC_API_KEY") == "from-dotenv"
    assert "load_dotenv()" in pathlib.Path("app/main.py").read_text()
