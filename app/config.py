import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./portal.db")

# Railway provides PostgreSQL URLs starting with postgres:// but SQLAlchemy
# requires postgresql://.  Fix transparently.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

APP_TITLE = os.getenv("APP_TITLE", "IT Demand Dashboard")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
