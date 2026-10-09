import os, tempfile
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL") or "sqlite:///" + tempfile.mktemp(suffix=".db")  # set TEST_DATABASE_URL to run the suite on PostgreSQL
os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"] = "a@x.com", "adminpass1"
os.environ.pop("ANTHROPIC_API_KEY", None)
