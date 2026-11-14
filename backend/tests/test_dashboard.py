from uuid import uuid4

import pytest
from app.ingestion.schemas import PaymentInput
from app.ingestion.service import create_payment
from app.main import create_app
from fastapi.testclient import TestClient


def test_dashboard_shell_and_missing_report(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYREVIVE_DEMO_REPORTS", str(tmp_path))
    with TestClient(create_app(probe=object())) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Payment overview" in response.text
        assert "frame-ancestors" in response.headers["Content-Security-Policy"]
        assert client.get("/dashboard/assets/dashboard.js").status_code == 200
        assert client.get("/dashboard/reports/diagnosis").status_code == 404


@pytest.mark.postgres
def test_overview_scopes_totals_and_paginates(engine, org):
    app = create_app(probe=object())
    app.state.engine = engine
    for reference in ("first", "second"):
        create_payment(engine, org, PaymentInput(reference=reference, amount_minor=123))
    with TestClient(app) as client:
        path = f"/v1/organizations/{org}/dashboard"
        first = client.get(path, params={"limit": 1}).json()
        second = client.get(path, params={"limit": 1, "offset": 1}).json()
        assert first["summary"] == {"pending": {"count": 2, "amount_minor": "246"}}
        assert first["has_more"] and not second["has_more"]
        assert first["payments"][0]["id"] != second["payments"][0]["id"]
        filtered = client.get(path, params={"status": "failed"}).json()
        assert filtered["payments"] == [] and filtered["summary"] == first["summary"]
        assert client.get(f"/v1/organizations/{uuid4()}/dashboard").status_code == 404
