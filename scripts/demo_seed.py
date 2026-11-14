"""Seed the local dashboard through the normal payment and recovery API."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from experiments.replay import ExperimentConfig
from experiments.report import run
from ml.analysis.degradation import run_detection
from simulator.artifacts import export_dataset
from simulator.config import GeneratorConfig
from simulator.ingest import ingest

ORG = "00000000-0000-0000-0000-000000000001"


def seed(client: httpx.Client) -> dict:
    prefix = f"/v1/organizations/{ORG}"
    with TemporaryDirectory() as temporary:
        directory = Path(temporary) / "dataset"
        export_dataset(GeneratorConfig(customers=1800), directory)
        counts = ingest(directory, client, limit=65, include_features=True)
        root = Path(os.environ.get("PAYREVIVE_DEMO_REPORTS", "work/demo-reports"))
        if not (root / "detection").exists():
            run_detection(directory, root / "detection")
        if not (root / "experiment").exists():
            run(ExperimentConfig(runs=30, customers=300), root / "experiment")

    def send(method: str, path: str, **kwargs):
        response = client.request(method, prefix + path, **kwargs)
        response.raise_for_status()
        return response.json()

    customer = send(
        "POST",
        "/customers",
        json={
            "reference": "dashboard-demo-consenting",
            "retry_consent": True,
        },
    )
    payment = send(
        "POST",
        "/payments",
        json={
            "reference": "dashboard-demo-approval",
            "amount_minor": 149900,
            "customer_id": customer["id"],
        },
    )
    path = f"/payments/{payment['id']}"
    timeline = send("GET", path)
    if not timeline["events"]:
        send(
            "POST",
            path + "/events",
            json={
                "source_event_id": "dashboard-demo-approval-first-attempt",
                "attempt_number": 1,
                "status": "failed",
                "reason": "issuer_unavailable",
                "occurred_at": (datetime.now(UTC) - timedelta(hours=8)).isoformat(),
            },
        )
    if not timeline["recovery_decisions"]:
        send("POST", path + "/recommend")
    return {"ingestion": counts, "approval_payment_id": payment["id"]}


if __name__ == "__main__":
    with httpx.Client(base_url="http://localhost:8000", timeout=15) as api:
        print(json.dumps(seed(api), indent=2))
