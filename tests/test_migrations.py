from alembic import command
from sqlalchemy import create_engine, inspect, text
from app.db_migrate import config, migrate


def seed(eng):
    with eng.begin() as c:
        c.execute(text("INSERT INTO user (username,email,password,role,approved) VALUES ('keep','k@x.com','salt$hash','builder',1)"))
        c.execute(text("INSERT INTO wasterequest (builder_id,waste_type,tons,location,pickup_date,status,tracking_id) VALUES (1,'Concrete',3.0,'Pune','2026-10-01','Completed','WM-2026-000001')"))
        c.execute(text("INSERT INTO wasteclassification (request_id,material,confidence,recyclable,recommended_action,possible_reuse,created_at) VALUES (1,'Concrete',0.9,1,'Recycle','',CURRENT_TIMESTAMP)"))


def test_existing_database_upgrades_in_place(tmp_path):
    url = f"sqlite:///{tmp_path}/old.db"
    command.upgrade(config(url), "0001")                         # schema exactly as it was before migrations
    eng = create_engine(url); seed(eng)
    with eng.begin() as c:
        c.execute(text("DROP TABLE alembic_version"))            # simulate a DB created before Alembic existed
    migrate(url)
    with eng.connect() as c:
        assert c.execute(text("SELECT username,password FROM user")).one() == ("keep", "salt$hash")
        assert c.execute(text("SELECT tracking_id,tons FROM wasterequest")).one() == ("WM-2026-000001", 3.0)
        assert c.execute(text("SELECT confidence FROM wasteclassification")).scalar() == 0.9
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0003"
    with eng.begin() as c:                                       # new schema allows an unknown confidence
        c.execute(text("INSERT INTO wasteclassification (request_id,material,confidence,recyclable,recommended_action,possible_reuse,created_at) VALUES (1,'Wood',NULL,1,'x','',CURRENT_TIMESTAMP)"))
    with eng.connect() as c:
        assert c.execute(text("SELECT lat, lng FROM wasterequest")).one() == (None, None)   # new columns are nullable
        assert c.execute(text("SELECT count(*) FROM requestphoto")).scalar() == 0
    migrate(url)                                                 # idempotent


def test_fresh_database(tmp_path):
    url = f"sqlite:///{tmp_path}/new.db"; migrate(url)
    assert {"user", "wasterequest", "notification", "auditlog", "environmentalfactor", "wasteclassification"} <= set(inspect(create_engine(url)).get_table_names())
