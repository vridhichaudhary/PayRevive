import hashlib
import json
import shutil

import httpx
import pytest
from PIL import Image

from simulator import artifacts
from simulator.artifacts import export_dataset
from simulator.config import GeneratorConfig
from simulator.ingest import ingest


@pytest.fixture
def exported(tmp_path):
    output = tmp_path / "dataset"
    export_dataset(GeneratorConfig(customers=30), output)
    return output


def test_complete_exports_are_byte_identical_and_manifest_checksums_match(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    export_dataset(GeneratorConfig(customers=30), a)
    export_dataset(GeneratorConfig(customers=30), b)
    manifest = json.loads((a / "manifest.json").read_text())
    assert manifest == json.loads((b / "manifest.json").read_text())
    for name, checksum in manifest["files"].items():
        assert (a / name).read_bytes() == (b / name).read_bytes()
        assert hashlib.sha256((a / name).read_bytes()).hexdigest() == checksum
    with Image.open(a / "reports/incidents.png") as image:
        image.verify()


def test_existing_output_is_never_overwritten(exported):
    before = (exported / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        export_dataset(GeneratorConfig(customers=30), exported)
    assert (exported / "manifest.json").read_bytes() == before


def test_rendering_failure_does_not_publish_partial_artifacts(monkeypatch, tmp_path):
    def fail(*args):
        raise RuntimeError("Injected renderer failure")

    monkeypatch.setattr(artifacts, "render_incidents", fail)
    with pytest.raises(RuntimeError, match="Injected"):
        export_dataset(GeneratorConfig(customers=30), tmp_path / "dataset")
    assert list(tmp_path.iterdir()) == []


def fake_server():
    payments, events, requests = {}, set(), []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if request.url.path.endswith("/payments"):
            assert set(body) == {"reference", "amount_minor", "currency"}
            duplicate = body["reference"] in payments
            payments.setdefault(body["reference"], str(len(payments)))
            return httpx.Response(
                200 if duplicate else 201, json={"id": payments[body["reference"]]}
            )
        assert set(body) == {
            "source_event_id",
            "provider",
            "account",
            "attempt_number",
            "status",
            "occurred_at",
            "reason",
        }
        key = (body["account"], body["source_event_id"])
        duplicate = key in events
        events.add(key)
        return httpx.Response(200 if duplicate else 201, json={"duplicate": duplicate})

    return httpx.Client(
        base_url="http://localhost:8000", transport=httpx.MockTransport(handle)
    ), requests


def test_ingestion_replays_and_never_reads_hidden_truth(exported):
    shutil.rmtree(exported / "truth")
    shutil.rmtree(exported / "reports")
    client, requests = fake_server()
    with client:
        first = ingest(exported, client, limit=5)
        replay = ingest(exported, client, limit=5)
    assert first == {
        "payments_created": 5,
        "payments_replayed": 0,
        "events_created": 5,
        "events_replayed": 0,
    }
    assert replay == {
        "payments_created": 0,
        "payments_replayed": 5,
        "events_created": 0,
        "events_replayed": 5,
    }
    assert all("true_failure_category" not in request for request in requests)


def test_corrupt_artifact_is_rejected_before_any_http_write(exported):
    path = exported / "operational/payments.jsonl"
    path.write_bytes(path.read_bytes() + b"corruption")
    client, requests = fake_server()
    with client, pytest.raises(ValueError, match="Checksum mismatch"):
        ingest(exported, client, limit=1)
    assert requests == []


def test_relationship_tampering_is_rejected_even_if_checksum_is_updated(exported):
    path = exported / "operational/attempts.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[-1]["payment_ref"] = "does_not_exist"
    content = b"".join(artifacts.canonical(row) for row in rows)
    path.write_bytes(content)
    manifest_path = exported / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["operational/attempts.jsonl"] = hashlib.sha256(content).hexdigest()
    manifest_path.write_bytes(artifacts.canonical(manifest))
    client, requests = fake_server()
    with client, pytest.raises(ValueError, match="relationship mismatch"):
        ingest(exported, client, limit=1)
    assert requests == []


def test_ingestion_rejects_nonlocal_destination_before_loading_files(tmp_path):
    with httpx.Client(base_url="https://example.com") as client:
        with pytest.raises(ValueError, match="local simulator"):
            ingest(tmp_path, client)


def test_no_incident_configuration_exports_a_valid_report(tmp_path):
    summary = export_dataset(
        GeneratorConfig(customers=30, incident_strength=0), tmp_path / "dataset"
    )
    assert summary["incidents"] == []
