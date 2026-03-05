"""Tests for web (HTML) routes — auth guards and page rendering."""


class TestAuthGuards:
    """Unauthenticated users should be redirected to /login."""

    def test_dashboard_redirects(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_proceedings_redirects(self, client):
        resp = client.get("/proceedings", follow_redirects=False)
        assert resp.status_code == 302

    def test_deadlines_redirects(self, client):
        resp = client.get("/deadlines", follow_redirects=False)
        assert resp.status_code == 302

    def test_demands_redirects(self, client):
        resp = client.get("/demands", follow_redirects=False)
        assert resp.status_code == 302

    def test_clients_redirects(self, client):
        resp = client.get("/clients", follow_redirects=False)
        assert resp.status_code == 302

    def test_sync_log_redirects(self, client):
        resp = client.get("/sync-log", follow_redirects=False)
        assert resp.status_code == 302


class TestLoginPage:
    def test_login_page_renders(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"PAN" in resp.content or b"pan" in resp.content

    def test_login_invalid_pan_format(self, client):
        resp = client.post(
            "/login",
            data={"pan": "INVALID", "password": "test123"},
            follow_redirects=False,
        )
        # Should re-render login page with error (not redirect)
        assert resp.status_code == 200
        assert b"Invalid PAN" in resp.content


class TestOtpPage:
    def test_otp_redirects_without_session(self, client):
        resp = client.get("/verify-otp", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]


class TestSyncStatus:
    def test_sync_status_endpoint(self, client):
        resp = client.get("/sync-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "message" in data


class TestLogout:
    def test_logout_redirects_to_login(self, client):
        resp = client.get("/logout", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]


class TestClientDetailPage:
    def test_client_not_found(self, client):
        # Simulate logged-in session
        client.cookies.set("session", "")
        resp = client.get("/clients/NONEXIST00", follow_redirects=False)
        # Will redirect to login since not authenticated
        assert resp.status_code == 302


class TestFormatInr:
    """Test the INR formatting helper."""

    def test_format_inr_import(self):
        from app.main import _format_inr
        assert _format_inr(None) == "\u2014"
        assert _format_inr(0) == "\u20b90.00"
        assert _format_inr(999) == "\u20b9999.00"
        assert _format_inr(1000) == "\u20b91,000.00"
        assert _format_inr(100000) == "\u20b91,00,000.00"
        assert _format_inr(1234567) == "\u20b912,34,567.00"
        assert _format_inr(12345678.50) == "\u20b91,23,45,678.50"

    def test_format_inr_negative(self):
        from app.main import _format_inr
        result = _format_inr(-1000)
        assert result.startswith("-")
