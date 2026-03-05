"""Tests for the REST API endpoints (/api/*)."""

from datetime import date, timedelta


class TestHealthCheck:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestApiStats:
    def test_stats_empty_db(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_clients"] == 0
        assert data["pending_proceedings"] == 0
        assert data["overdue_proceedings"] == 0
        assert data["total_outstanding_demand"] == 0

    def test_stats_with_data(self, client, seeded_db):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_clients"] == 3
        assert data["pending_proceedings"] == 3  # 2 for ABCDE + 1 for KLMNO
        assert data["overdue_proceedings"] == 1  # 1 overdue for ABCDE
        # outstanding + disputed demands: 575000 + 28000 + 1700000 = 2303000
        assert data["total_outstanding_demand"] == 2303000.0


class TestApiProceedings:
    def test_proceedings_empty(self, client):
        resp = client.get("/api/proceedings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_proceedings_with_data(self, client, seeded_db):
        resp = client.get("/api/proceedings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4

    def test_proceedings_filter_by_pan(self, client, seeded_db):
        resp = client.get("/api/proceedings?pan=ABCDE1234F")
        data = resp.json()
        assert data["total"] == 2
        assert all(p["pan"] == "ABCDE1234F" for p in data["items"])

    def test_proceedings_filter_by_status(self, client, seeded_db):
        resp = client.get("/api/proceedings?status=closed")
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "closed"

    def test_proceedings_filter_by_ay(self, client, seeded_db):
        resp = client.get("/api/proceedings?ay=2024-25")
        data = resp.json()
        assert data["total"] == 2

    def test_proceedings_pagination(self, client, seeded_db):
        resp = client.get("/api/proceedings?limit=2&offset=0")
        data = resp.json()
        assert data["total"] == 4
        assert len(data["items"]) == 2

        resp2 = client.get("/api/proceedings?limit=2&offset=2")
        data2 = resp2.json()
        assert len(data2["items"]) == 2

    def test_proceedings_item_fields(self, client, seeded_db):
        resp = client.get("/api/proceedings?limit=1")
        item = resp.json()["items"][0]
        expected_keys = {
            "id", "pan", "client_name", "assessment_year", "notice_type",
            "section", "date_of_issue", "response_due_date", "status",
            "days_until_due", "urgency",
        }
        assert set(item.keys()) == expected_keys


class TestApiDemands:
    def test_demands_empty(self, client):
        resp = client.get("/api/demands")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    def test_demands_with_data(self, client, seeded_db):
        resp = client.get("/api/demands")
        data = resp.json()
        assert data["total"] == 3

    def test_demands_filter_by_status(self, client, seeded_db):
        resp = client.get("/api/demands?status=disputed")
        data = resp.json()
        assert data["total"] == 1

    def test_demands_item_fields(self, client, seeded_db):
        resp = client.get("/api/demands?limit=1")
        item = resp.json()["items"][0]
        expected_keys = {
            "id", "pan", "client_name", "assessment_year", "section",
            "demand_amount", "interest_amount", "total_amount",
            "ao_name", "ao_jurisdiction", "status", "urgency_level",
        }
        assert set(item.keys()) == expected_keys

    def test_demands_sorted_by_amount_desc(self, client, seeded_db):
        resp = client.get("/api/demands")
        items = resp.json()["items"]
        amounts = [i["total_amount"] for i in items]
        assert amounts == sorted(amounts, reverse=True)


class TestApiClients:
    def test_clients_empty(self, client):
        resp = client.get("/api/clients")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_clients_with_data(self, client, seeded_db):
        resp = client.get("/api/clients")
        data = resp.json()
        assert len(data) == 3
        # Should be sorted by name
        names = [c["name"] for c in data]
        assert names == sorted(names)

    def test_client_fields(self, client, seeded_db):
        resp = client.get("/api/clients")
        item = resp.json()[0]
        expected_keys = {"pan", "name", "last_synced", "open_proceedings", "total_demand"}
        assert set(item.keys()) == expected_keys


class TestApiDeadlines:
    def test_deadlines_empty(self, client):
        resp = client.get("/api/deadlines")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_deadlines_with_data(self, client, seeded_db):
        resp = client.get("/api/deadlines?days=365")
        data = resp.json()
        # Should include pending proceedings with due dates within 365 days
        # Overdue ones also have due dates <= cutoff
        assert len(data) >= 1

    def test_deadlines_filters_closed(self, client, seeded_db):
        resp = client.get("/api/deadlines?days=365")
        data = resp.json()
        for item in data:
            assert item["urgency"] != "none"  # closed proceedings have urgency "none"
