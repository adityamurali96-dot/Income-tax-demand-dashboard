"""Tests for SQLAlchemy models and their computed properties."""

from datetime import date, timedelta
from app.models import Client, Proceeding, Demand


class TestClientModel:
    def test_open_proceedings_count(self, db_session, sample_client):
        db_session.add(Proceeding(
            pan=sample_client.pan, assessment_year="2024-25",
            notice_type="scrutiny", section="143(2)", status="pending",
        ))
        db_session.add(Proceeding(
            pan=sample_client.pan, assessment_year="2023-24",
            notice_type="demand", section="156", status="closed",
        ))
        db_session.commit()
        db_session.refresh(sample_client)
        assert sample_client.open_proceedings_count == 1

    def test_total_demand_amount(self, db_session, sample_client):
        db_session.add(Demand(
            pan=sample_client.pan, assessment_year="2024-25",
            section="156", total_amount=100000, status="outstanding",
        ))
        db_session.add(Demand(
            pan=sample_client.pan, assessment_year="2023-24",
            section="156", total_amount=50000, status="closed",
        ))
        db_session.commit()
        db_session.refresh(sample_client)
        # Only non-closed demands count
        assert sample_client.total_demand_amount == 100000.0

    def test_total_demand_amount_empty(self, db_session, sample_client):
        assert sample_client.total_demand_amount == 0.0


class TestProceedingModel:
    def test_days_until_due_future(self, sample_proceeding):
        assert sample_proceeding.days_until_due > 0

    def test_days_until_due_none(self, db_session, sample_client):
        p = Proceeding(
            pan=sample_client.pan, assessment_year="2024-25",
            notice_type="scrutiny", section="143(2)", status="pending",
            response_due_date=None,
        )
        assert p.days_until_due is None

    def test_is_overdue_true(self, db_session, sample_client):
        p = Proceeding(
            pan=sample_client.pan, assessment_year="2024-25",
            notice_type="scrutiny", section="143(2)", status="pending",
            response_due_date=date.today() - timedelta(days=5),
        )
        assert p.is_overdue is True

    def test_is_overdue_false_when_closed(self, db_session, sample_client):
        p = Proceeding(
            pan=sample_client.pan, assessment_year="2024-25",
            notice_type="scrutiny", section="143(2)", status="closed",
            response_due_date=date.today() - timedelta(days=5),
        )
        assert p.is_overdue is False

    def test_is_overdue_false_when_no_due_date(self, db_session, sample_client):
        p = Proceeding(
            pan=sample_client.pan, assessment_year="2024-25",
            notice_type="scrutiny", section="143(2)", status="pending",
        )
        assert p.is_overdue is False

    def test_urgency_critical_overdue(self, db_session, sample_client):
        p = Proceeding(
            pan=sample_client.pan, assessment_year="2024-25",
            notice_type="scrutiny", section="143(2)", status="pending",
            response_due_date=date.today() - timedelta(days=1),
        )
        assert p.urgency == "critical"

    def test_urgency_critical_within_3_days(self, db_session, sample_client):
        p = Proceeding(
            pan=sample_client.pan, assessment_year="2024-25",
            notice_type="scrutiny", section="143(2)", status="pending",
            response_due_date=date.today() + timedelta(days=2),
        )
        assert p.urgency == "critical"

    def test_urgency_high(self, db_session, sample_client):
        p = Proceeding(
            pan=sample_client.pan, assessment_year="2024-25",
            notice_type="scrutiny", section="143(2)", status="pending",
            response_due_date=date.today() + timedelta(days=5),
        )
        assert p.urgency == "high"

    def test_urgency_medium(self, db_session, sample_client):
        p = Proceeding(
            pan=sample_client.pan, assessment_year="2024-25",
            notice_type="scrutiny", section="143(2)", status="pending",
            response_due_date=date.today() + timedelta(days=10),
        )
        assert p.urgency == "medium"

    def test_urgency_low(self, db_session, sample_client):
        p = Proceeding(
            pan=sample_client.pan, assessment_year="2024-25",
            notice_type="scrutiny", section="143(2)", status="pending",
            response_due_date=date.today() + timedelta(days=30),
        )
        assert p.urgency == "low"

    def test_urgency_none_when_not_pending(self, db_session, sample_client):
        p = Proceeding(
            pan=sample_client.pan, assessment_year="2024-25",
            notice_type="scrutiny", section="143(2)", status="closed",
            response_due_date=date.today() + timedelta(days=5),
        )
        assert p.urgency == "none"


class TestDemandModel:
    def test_urgency_critical(self):
        d = Demand(total_amount=1500000, status="outstanding")
        assert d.urgency_level == "critical"

    def test_urgency_high(self):
        d = Demand(total_amount=500000, status="outstanding")
        assert d.urgency_level == "high"

    def test_urgency_medium(self):
        d = Demand(total_amount=50000, status="outstanding")
        assert d.urgency_level == "medium"

    def test_urgency_low(self):
        d = Demand(total_amount=5000, status="outstanding")
        assert d.urgency_level == "low"

    def test_urgency_zero(self):
        d = Demand(total_amount=0, status="outstanding")
        assert d.urgency_level == "low"

    def test_urgency_none_amount(self):
        d = Demand(total_amount=None, status="outstanding")
        assert d.urgency_level == "low"
