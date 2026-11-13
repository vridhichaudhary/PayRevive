"""Verified synthetic features -> real API -> immutable provenance -> replay."""

import json
import tempfile
from pathlib import Path

import httpx

from ml.data.loading import load_operational
from simulator.artifacts import export_dataset
from simulator.config import GeneratorConfig
from simulator.ingest import ingest, payment_payload


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="payrevive-features-") as temporary:
        directory = Path(temporary) / "dataset"
        export_dataset(GeneratorConfig(customers=30), directory)
        data = load_operational(directory)
        with httpx.Client(base_url="http://localhost:8000", timeout=15) as client:
            first = ingest(directory, client, limit=5, include_features=True)
            replay = ingest(directory, client, limit=5, include_features=True)
            assert first["features_created"] + first["features_replayed"] == 5
            assert replay["features_created"] == 0 and replay["features_replayed"] == 5
            payment = data.payments[0]
            path = f"/v1/organizations/{payment.org_id}/payments"
            response = client.post(path, json=payment_payload(payment))
            response.raise_for_status()
            payment_id = response.json()["id"]
            response = client.get(f"{path}/{payment_id}")
            response.raise_for_status()
            snapshots = response.json()["feature_snapshots"]
            assert len(snapshots) == 1
            assert snapshots[0]["source_manifest_sha256"] == data.manifest_sha256
            assert snapshots[0]["values"] == data.features[0].model_dump(mode="json")
            assert snapshots[0]["schema_version"] == "features-v1"
            assert response.json()["customer_id"] is None
    print(json.dumps({"payment_id": payment_id, "dataset_id": data.manifest.dataset_id}))
    print("PASS: verified feature ingestion, event binding, immutable provenance, and replay")


if __name__ == "__main__":
    main()
