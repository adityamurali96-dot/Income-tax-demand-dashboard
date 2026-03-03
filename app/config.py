import os
from dotenv import load_dotenv

load_dotenv()

# Railway PostgreSQL addon exposes DATABASE_PRIVATE_URL for internal networking
# (lower latency) and DATABASE_URL for external access.  Prefer the private URL
# when available, then fall back to DATABASE_URL, then to local SQLite.
DATABASE_URL = (
    os.getenv("DATABASE_PRIVATE_URL")
    or os.getenv("DATABASE_URL")
    or "sqlite:///./portal.db"
)

# Railway provides PostgreSQL URLs starting with postgres:// but SQLAlchemy
# requires postgresql://.  Fix transparently.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

APP_TITLE = os.getenv("APP_TITLE", "IT Demand Dashboard")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
