import json
from datetime import timedelta
from uuid import uuid4

import pytest
from app.features.contracts import SnapshotInput
from app.features.service import record_snapshot
from app.ingestion.schemas import EventInput, PaymentInput
from app.ingestion.service import create_payment, ingest_event
from app.main import create_app
from fastapi.testclient import TestClient

from simulator.config import GeneratorConfig
from simulator.generator import generate


@pytest.mark.postgres
def test_exposure_matches_source_time_cohort_and_organization(engine, org, tmp_path, monkeypatch):
    data = generate(GeneratorConfig(customers=30, org_id=org))
    p, a, f = data.payments[0], data.attempts[0], data.features[0]
    payment = create_payment(
        engine, org, PaymentInput(reference=p.payment_ref, amount_minor=p.amount_minor)
    )[0]
    event = ingest_event(
        engine, org, payment["id"], EventInput(**a.model_dump(exclude={"payment_ref", "org_id"}))
    )
    record_snapshot(
        engine,
        org,
        payment["id"],
        event["event_id"],
        SnapshotInput(
            source_dataset_id=data.dataset_id,
            source_manifest_sha256="a" * 64,
            generator_version="1.0.0",
            observation=f,
        ),
    )
    path = tmp_path / str(org) / "detection"
    path.mkdir(parents=True)
    row = dict(
        incident_id="test",
        scope="issuer",
        target=f.issuer,
        starts_at=f.observed_at.isoformat(),
        ends_at=(f.observed_at + timedelta(hours=1)).isoformat(),
    )
    report = dict(
        synthetic_only=True,
        provenance=dict(
            generator_config=dict(org_id=str(org)),
            dataset_id=data.dataset_id,
            source_manifest_sha256="a" * 64,
        ),
        evaluation=dict(methods=dict(rolling=dict(incidents=[row]))),
    )
    (path / "detection.json").write_text(json.dumps(report))
    monkeypatch.setenv("PAYREVIVE_DEMO_REPORTS", str(tmp_path))
    app = create_app(probe=object())
    app.state.engine = engine
    with TestClient(app) as client:
        base = f"/v1/organizations/{org}/workbench"
        response = client.get(base + "/cases", params={"incident_id": "test"}).json()
        assert response["count"] == 1
        assert response["amount_minor"] == str(p.amount_minor)
        assert response["match"] == "exposure"
        assert client.get(base + "/incidents").json()["generated_at"]
        assert client.get(base + "/cases", params={"incident_id": "missing"}).status_code == 404
        assert (
            client.get(base + "/cases", params={"until": f.observed_at.isoformat()}).json()["count"]
            == 0
        )
        report["provenance"]["source_manifest_sha256"] = "b" * 64
        (path / "detection.json").write_text(json.dumps(report))
        assert client.get(base + "/cases", params={"incident_id": "test"}).json()["count"] == 0
        assert (
            client.get(f"/v1/organizations/{uuid4()}/workbench/cases/{payment['id']}").status_code
            == 404
        )
