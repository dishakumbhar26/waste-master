from dotenv import load_dotenv
load_dotenv()
from alembic import context
from sqlalchemy import create_engine
from sqlmodel import SQLModel
from app.config import database_url
import app.models  # noqa: F401  (registers tables on SQLModel.metadata)

url = context.config.get_main_option("sqlalchemy.url") or database_url()
eng = create_engine(url)
with eng.connect() as conn:
    context.configure(connection=conn, target_metadata=SQLModel.metadata, render_as_batch=True)  # batch = SQLite-safe ALTERs
    with context.begin_transaction():
        context.run_migrations()
