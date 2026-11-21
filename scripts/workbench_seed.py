"""Create an isolated organization and a current, reproducible synthetic exposure demo."""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import httpx
from app.config import get_settings
from app.db import build_engine
from app.domain.tables import organizations
from sqlalchemy.dialects.postgresql import insert

from simulator.generator import canonical

ORG = UUID("00000000-0000-0000-0000-000000000006")


def seed(client: httpx.Client):
    engine = build_engine(get_settings())
    with engine.begin() as c:
        c.execute(
            insert(organizations)
            .values(id=ORG, name="Workbench synthetic demo")
            .on_conflict_do_nothing(index_elements=[organizations.c.id])
        )
    engine.dispose()
    base = f"/v1/organizations/{ORG}"

    def send(method, path, payload=None):
        r = client.request(method, base + path, json=payload)
        r.raise_for_status()
        return r.json()

    ref = "workbench-issuer-exposure"
    customer = send("POST", "/customers", {"reference": ref, "retry_consent": True})
    payment = send(
        "POST",
        "/payments",
        {
            "reference": ref,
            "amount_minor": 149900,
            "customer_id": customer["id"],
        },
    )
    detail = send("GET", f"/payments/{payment['id']}")
    start = (datetime.now(UTC) - timedelta(hours=8)).replace(minute=0, second=0, microsecond=0)
    if detail["events"]:
        start = datetime.fromisoformat(detail["events"][0]["occurred_at"])
    end = start + timedelta(hours=1)
    identity = hashlib.sha256(canonical({"org": str(ORG), "at": start.isoformat()})).hexdigest()
    dataset = identity[:16]
    event = send(
        "POST",
        f"/payments/{payment['id']}/events",
        {
            "source_event_id": ref,
            "account": "gateway_demo",
            "attempt_number": 1,
            "status": "failed",
            "reason": "issuer_unavailable",
            "occurred_at": start.isoformat(),
        },
    )
    snapshot = {
        "schema_version": "features-v1",
        "source_dataset_id": dataset,
        "source_manifest_sha256": identity,
        "generator_version": "1.0.0",
        "observation": {
            "payment_ref": ref,
            "source_event_id": ref,
            "org_id": str(ORG),
            "observed_at": start.isoformat(),
            "amount_minor": 149900,
            "segment": "standard",
            "issuer": "issuer_demo",
            "network": "network_demo",
            "gateway": "gateway_demo",
            "tenure_months": 12,
            "days_to_expiry": 100,
            "day_of_month": start.day,
            "hour_utc": start.hour,
            "gateway_latency_ms": 800,
            "prior_payment_count": 0,
            "prior_success_rate": None,
        },
    }
    send("POST", f"/payments/{payment['id']}/events/{event['event_id']}/features", snapshot)
    for index in range(23):
        reference = f"workbench-exposure-{index:02}"
        extra = send("POST", "/payments", {"reference": reference, "amount_minor": 49900})
        outcome = send(
            "POST",
            f"/payments/{extra['id']}/events",
            {
                "source_event_id": reference,
                "account": "gateway_demo",
                "attempt_number": 1,
                "status": "failed" if index < 11 else "succeeded",
                "reason": "issuer_unavailable" if index < 11 else None,
                "occurred_at": start.isoformat(),
            },
        )
        extra_snapshot = {
            **snapshot,
            "observation": {
                **snapshot["observation"],
                "payment_ref": reference,
                "source_event_id": reference,
                "amount_minor": 49900,
            },
        }
        send(
            "POST", f"/payments/{extra['id']}/events/{outcome['event_id']}/features", extra_snapshot
        )
    send("POST", f"/payments/{payment['id']}/recommend")
    root = Path(os.environ.get("PAYREVIVE_DEMO_REPORTS", "work/demo-reports"))
    directory = root / str(ORG) / "detection"
    directory.mkdir(parents=True, exist_ok=True)
    row = {
        "incident_id": "issuer-demo",
        "scope": "issuer",
        "target": "issuer_demo",
        "starts_at": start.isoformat(),
        "ends_at": end.isoformat(),
        "first_alert_at": None,
        "delay_hours": None,
        "eligible_windows": 0,
    }
    report = {
        "synthetic_only": True,
        "report_version": "degradation-v1",
        "provenance": {
            "dataset_id": dataset,
            "source_manifest_sha256": identity,
            "generator_config": {"org_id": str(ORG)},
            "source": "guided exposure fixture; zero detector performance claims",
        },
        "evaluation": {"methods": {"rolling": {"incidents": [row]}}},
    }
    temporary = directory / f".fixture-{uuid5(ORG, str(payment['id']))}.json"
    temporary.write_bytes(canonical(report))
    temporary.replace(directory / "detection.json")
    return {
        "org_id": str(ORG),
        "payment_id": payment["id"],
        "url": f"/?org={ORG}#case/{payment['id']}",
    }


if __name__ == "__main__":
    with httpx.Client(base_url="http://localhost:8000", timeout=15) as client:
        print(json.dumps(seed(client), indent=2))
