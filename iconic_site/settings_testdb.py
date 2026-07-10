from .settings import *  # noqa: F401,F403

DATABASES["default"]["NAME"] = BASE_DIR / "db_rehearsal.sqlite3"
