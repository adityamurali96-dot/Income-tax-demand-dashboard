"""
Shared fixtures for testing the IT Demand Dashboard.
Uses an in-memory SQLite database so tests are isolated and fast.
"""

import pytest
from datetime import date, datetime, timedelta
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Client, Proceeding, Demand, SyncLog


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_engine():
    # StaticPool ensures all connections share the same in-memory database
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def client(app):
    """FastAPI TestClient with overridden DB dependency."""
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture()
def app(db_engine):
    """FastAPI app with DB dependency overridden to use test database."""
    from app.main import app as fastapi_app

    Session = sessionmaker(bind=db_engine)

    def _override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_client(db_session):
    c = Client(
        pan="ABCDE1234F",
        name="Test Client",
        email="test@example.com",
        phone="9876543210",
    )
    db_session.add(c)
    db_session.commit()
    return c


@pytest.fixture()
def sample_proceeding(db_session, sample_client):
    p = Proceeding(
        pan=sample_client.pan,
        assessment_year="2024-25",
        notice_type="scrutiny",
        section="143(2)",
        date_of_issue=date.today() - timedelta(days=30),
        response_due_date=date.today() + timedelta(days=10),
        status="pending",
    )
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture()
def sample_demand(db_session, sample_client):
    d = Demand(
        pan=sample_client.pan,
        assessment_year="2024-25",
        section="156",
        demand_amount=100000,
        interest_amount=15000,
        total_amount=115000,
        status="outstanding",
    )
    db_session.add(d)
    db_session.commit()
    return d


@pytest.fixture()
def seeded_db(db_session):
    """Seed the test DB with multiple clients, proceedings, and demands."""
    clients_data = [
        ("ABCDE1234F", "Rajesh Kumar"),
        ("FGHIJ5678K", "Priya Sharma"),
        ("KLMNO9012P", "Vikram Singh"),
    ]
    clients = []
    for pan, name in clients_data:
        c = Client(pan=pan, name=name)
        db_session.add(c)
        clients.append(c)
    db_session.flush()

    today = date.today()
    # Overdue proceeding
    db_session.add(Proceeding(
        pan="ABCDE1234F", assessment_year="2023-24", notice_type="scrutiny",
        section="143(2)", date_of_issue=today - timedelta(days=60),
        response_due_date=today - timedelta(days=5), status="pending",
    ))
    # Upcoming proceeding
    db_session.add(Proceeding(
        pan="ABCDE1234F", assessment_year="2024-25", notice_type="demand",
        section="156", date_of_issue=today - timedelta(days=10),
        response_due_date=today + timedelta(days=3), status="pending",
    ))
    # Closed proceeding
    db_session.add(Proceeding(
        pan="FGHIJ5678K", assessment_year="2023-24", notice_type="intimation",
        section="143(1)(a)", date_of_issue=today - timedelta(days=90),
        response_due_date=today - timedelta(days=30), status="closed",
    ))
    # Far future proceeding
    db_session.add(Proceeding(
        pan="KLMNO9012P", assessment_year="2024-25", notice_type="rectification",
        section="154", date_of_issue=today - timedelta(days=5),
        response_due_date=today + timedelta(days=60), status="pending",
    ))

    # Demands
    db_session.add(Demand(
        pan="ABCDE1234F", assessment_year="2024-25", section="156",
        demand_amount=500000, interest_amount=75000, total_amount=575000,
        status="outstanding",
    ))
    db_session.add(Demand(
        pan="FGHIJ5678K", assessment_year="2023-24", section="143(1)",
        demand_amount=25000, interest_amount=3000, total_amount=28000,
        status="disputed",
    ))
    db_session.add(Demand(
        pan="KLMNO9012P", assessment_year="2023-24", section="156",
        demand_amount=1500000, interest_amount=200000, total_amount=1700000,
        status="outstanding",
    ))

    db_session.commit()
    return db_session
