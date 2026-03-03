from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import DATABASE_URL

_is_sqlite = DATABASE_URL.startswith("sqlite")

connect_args = {}
if _is_sqlite:
    connect_args["check_same_thread"] = False

# PostgreSQL benefits from a bounded connection pool; SQLite doesn't use one.
pool_kwargs = {}
if not _is_sqlite:
    pool_kwargs.update(pool_size=5, max_overflow=10)

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    **pool_kwargs,
)

# Enable WAL mode for SQLite for better concurrent read performance
if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
