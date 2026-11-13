"""Generate, ingest, and replay a small population against the real API."""

import tempfile
from pathlib import Path

import httpx

from simulator.artifacts import export_dataset
from simulator.config import GeneratorConfig
from simulator.ingest import ingest


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="payrevive-smoke-") as temporary:
        output = Path(temporary) / "dataset"
        export_dataset(GeneratorConfig(customers=30), output)
        with httpx.Client(base_url="http://localhost:8000", timeout=15) as client:
            first = ingest(output, client, limit=10)
            replay = ingest(output, client, limit=10)
        assert first["payments_created"] + first["payments_replayed"] == 10
        assert first["events_created"] + first["events_replayed"] == 10
        assert replay == {
            "payments_created": 0,
            "payments_replayed": 10,
            "events_created": 0,
            "events_replayed": 10,
        }
    print("PASS: generated population, real API ingestion, and idempotent replay")


if __name__ == "__main__":
    main()
