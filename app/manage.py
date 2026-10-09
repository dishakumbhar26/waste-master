"""Usage: python -m app.manage make-admin EMAIL   (creates or promotes an admin and sets a new password; data is kept)"""
import getpass, sys
from dotenv import load_dotenv
load_dotenv()
from sqlmodel import Session, create_engine, select  # noqa: E402
from app.config import database_url  # noqa: E402
from app.db_migrate import migrate  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hp  # noqa: E402
from app.services.notification_service import audit  # noqa: E402


def main(argv):
    if len(argv) != 3 or argv[1] != "make-admin":
        sys.exit(__doc__)
    email, url = argv[2].strip().lower(), database_url()
    pw = getpass.getpass("New admin password (8+ chars): ")
    if len(pw) < 8 or pw != getpass.getpass("Repeat: "):
        sys.exit("Passwords must match and be at least 8 characters.")
    migrate(url)
    with Session(create_engine(url)) as s:
        u = s.exec(select(User).where(User.email == email)).first() or User(username="admin", email=email, password="", role="admin")
        u.role, u.approved, u.password = "admin", True, hp(pw)
        s.add(u); s.commit(); s.refresh(u)
        audit(s, u, "admin set via manage.py", "user", u.id); s.commit()
    print(f"Admin ready: {email}")


if __name__ == "__main__":
    main(sys.argv)
