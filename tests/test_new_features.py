import io, re
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from app.main import app, engine
from app.models import RequestPhoto, WasteClassification, WasteRequest, User
from app.services.geo import parse_coords
from tests.test_flow import post, tok, user


def img(fmt="JPEG", size=(3000, 2000), exif=False):
    b = io.BytesIO(); im = Image.new("RGB", size, (200, 120, 40))
    kw = {}
    if exif:
        e = Image.Exif(); e[0x8825] = {1: "N", 2: (18.0, 31.0, 0.0)}; kw["exif"] = e   # GPS IFD
    im.save(b, fmt, **kw); return b.getvalue()


def create(c, photo=None, **extra):
    d = dict(waste_type="Concrete", tons=3, location="Pune", pickup_date="2031-03-10", csrf_token=tok(c), **extra)
    files = {"photo": photo} if photo else None
    return c.post("/requests/new", data=d, files=files)


def last_id(c):
    return max(int(i) for i in re.findall(r'/requests/(\d+)"', c.get("/dashboard").text))


def test_parse_coords():
    assert parse_coords("", "") == (None, None) and parse_coords(" 18.5204 ", "73.8567") == (18.5204, 73.8567)
    for bad in (("18", ""), ("x", "1"), ("91", "0"), ("0", "181"), ("nan", "0")):
        with pytest.raises(ValueError):
            parse_coords(*bad)


def test_photo_stored_displayed_private_and_sanitised():
    with TestClient(app) as b, TestClient(app) as o, TestClient(app) as anon:
        user(b, "pb", "builder"); user(o, "po", "builder")
        r = create(b, ("site.jpg", img(exif=True), "image/jpeg"), lat="18.5204", lng="73.8567")
        assert r.status_code == 200
        rid = last_id(b); page = b.get(f"/requests/{rid}").text
        assert f"/requests/{rid}/photo" in page and 'data-lat="18.5204"' in page
        ph = b.get(f"/requests/{rid}/photo"); assert ph.headers["content-type"] == "image/jpeg"
        im = Image.open(io.BytesIO(ph.content)); assert max(im.size) <= 1600 and not dict(im.getexif()) and not im.getexif().get_ifd(0x8825)
        assert o.get(f"/requests/{rid}/photo").status_code == 403 and anon.get(f"/requests/{rid}/photo").status_code == 403
        with Session(engine) as s:
            assert s.exec(select(RequestPhoto).where(RequestPhoto.request_id == rid)).one().size == len(ph.content)  # persisted in the DB


def test_photo_validation_png_replace_and_ownership():
    with TestClient(app) as b, TestClient(app) as o:
        user(b, "pv", "builder"); user(o, "pw", "builder")
        before = len(b.get("/dashboard").text)
        bad = create(b, ("x.jpg", b"not an image", "image/jpeg")); assert "not a valid" in bad.text
        assert "too large" in create(b, ("big.jpg", b"0" * 8_000_001, "image/jpeg")).text
        assert 'value="Pune"' in bad.text                                  # form keeps the user's input on error
        assert "Pune" not in b.get("/dashboard").text                      # nothing was created
        assert create(b, ("a.png", img("PNG", (50, 50)), "image/png")).status_code == 200
        rid = last_id(b)
        assert b.get(f"/requests/{rid}/photo").content[:2] == b"\xff\xd8"  # stored as JPEG
        up = lambda c, data: c.post(f"/requests/{rid}/photo", data={"csrf_token": tok(c)}, files={"photo": data})
        assert up(o, ("a.jpg", img(size=(40, 40)), "image/jpeg")).status_code == 403
        assert "Photo saved" in up(b, ("a.jpg", img(size=(40, 40)), "image/jpeg")).text
        assert "not a valid" in up(b, ("a.txt", b"hello", "text/plain")).text
        with Session(engine) as s:
            assert len(s.exec(select(RequestPhoto).where(RequestPhoto.request_id == rid)).all()) == 1


def test_coordinates_validated_and_optional():
    with TestClient(app) as b:
        user(b, "gb", "builder")
        assert "both latitude and longitude" in create(b, lat="18.5").text
        assert "between -90 and 90" in create(b, lat="95", lng="10").text
        assert create(b).status_code == 200                                # no coordinates at all is fine
        assert "Not provided" in b.get(f"/requests/{last_id(b)}").text


def test_monthly_analytics_and_report():
    with TestClient(app) as b, TestClient(app) as p, TestClient(app) as a, TestClient(app) as anon:
        user(b, "ab", "builder"); user(p, "ap", "provider"); post(a, "/login", email="a@x.com", password="adminpass1")
        with Session(engine) as s:
            post(a, f"/admin/approve/{s.exec(select(User.id).where(User.email == 'ap@x.com')).one()}")
        for tons in (4, 2):
            b.post("/requests/new", data=dict(waste_type="Metal", tons=tons, location="Pune", pickup_date="2032-05-10", csrf_token=tok(b)))
        done = last_id(b) - 1
        post(p, f"/requests/{done}/accept"); post(p, f"/requests/{done}/advance"); post(p, f"/requests/{done}/advance")
        page = a.get("/analytics?date_from=2032-05-01&date_to=2032-05-31").text
        assert "2032-05" in page and "Recycling statistics" in page and "67%" in page            # diverted 4 of 6 t
        rep = a.get("/analytics/report?month=2032-05&fmt=csv")
        assert rep.headers["content-type"].startswith("text/csv") and "wastemaster-report-2032-05.csv" in rep.headers["content-disposition"]
        txt = rep.text; assert "Requests,2" in txt and "Completed jobs,1" in txt and "Tonnes requested,6.0" in txt and "Tonnes diverted,4.0" in txt and "methodology under review" in txt
        assert a.get("/analytics/report?month=2032-05&fmt=pdf").content[:4] == b"%PDF"
        assert "Requests,0" in a.get("/analytics/report?month=2001-01").text                     # empty month still valid
        assert "Choose a month" in a.get("/analytics/report?month=2032-13").text
        assert anon.get("/analytics/report?month=2032-05").status_code == 403


def test_historical_ai_records_survive_and_manifest_still_works():
    with TestClient(app) as b, TestClient(app) as p, TestClient(app) as a:
        user(b, "hb", "builder"); user(p, "hp", "provider"); post(a, "/login", email="a@x.com", password="adminpass1")
        with Session(engine) as s:
            post(a, f"/admin/approve/{s.exec(select(User.id).where(User.email == 'hp@x.com')).one()}")
        create(b); rid = last_id(b)
        with Session(engine) as s:
            s.add(WasteClassification(request_id=rid, material="Concrete", confidence=None, recyclable=True, recommended_action="x")); s.commit()
        for step in ("accept", "advance", "advance"):
            post(p, f"/requests/{rid}/{step}")
        assert b.get(f"/requests/{rid}/manifest.pdf").content[:4] == b"%PDF"
        with Session(engine) as s:
            assert s.exec(select(WasteClassification).where(WasteClassification.request_id == rid)).one().material == "Concrete"
