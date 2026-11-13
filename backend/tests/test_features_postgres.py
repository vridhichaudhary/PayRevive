from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa
from app.config import Settings
from app.domain.tables import audit, feature_snapshots
from app.features.contracts import SnapshotInput
from app.features.service import read_snapshot, record_snapshot
from app.ingestion.schemas import EventInput, PaymentInput
from app.ingestion.service import DomainError, create_payment, ingest_event
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from simulator.config import GeneratorConfig
from simulator.generator import generate

pytestmark = pytest.mark.postgres


@pytest.fixture
def observation(engine, org):
    data = generate(GeneratorConfig(customers=30, org_id=org))
    payment, attempt, feature = data.payments[0], data.attempts[0], data.features[0]
    payment_id = create_payment(
        engine, org, PaymentInput(reference=payment.payment_ref, amount_minor=payment.amount_minor)
    )[0]["id"]
    event_id = ingest_event(
        engine, org, payment_id, EventInput(**attempt.model_dump(exclude={"payment_ref", "org_id"}))
    )["event_id"]
    body = SnapshotInput(
        source_dataset_id=data.dataset_id,
        source_manifest_sha256="a" * 64,
        generator_version="1.0.0",
        observation=feature,
    )
    return dict(engine=engine, org_id=org, payment_id=payment_id, event_id=event_id, data=body)


def test_concurrent_snapshots_and_replay_have_one_effect(observation):
    barrier = Barrier(4)

    def write(_):
        barrier.wait()
        return record_snapshot(**observation)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(write, range(4)))
    assert sum(not duplicate for _, duplicate in results) == 1
    assert len({row["id"] for row, _ in results}) == 1
    with observation["engine"].connect() as connection:
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(audit)
                .where(audit.c.org_id == observation["org_id"], audit.c.kind == "features.recorded")
            )
            == 1
        )


def test_changed_values_or_provenance_conflict(observation):
    record_snapshot(**observation)
    data = observation["data"]
    for changed in [
        data.model_copy(update={"source_manifest_sha256": "b" * 64}),
        data.model_copy(
            update={
                "observation": data.observation.model_copy(
                    update={"gateway_latency_ms": data.observation.gateway_latency_ms + 1}
                )
            }
        ),
    ]:
        with pytest.raises(DomainError) as error:
            record_snapshot(**{**observation, "data": changed})
        assert error.value.status == 409


@pytest.mark.parametrize(
    "changes",
    [
        {"amount_minor": 1},
        {"source_event_id": "wrong"},
        {"payment_ref": "wrong"},
        {"gateway": "wrong"},
        {"org_id": uuid4()},
    ],
)
def test_snapshot_must_match_server_evidence(observation, changes):
    data = observation["data"]
    changed = data.model_copy(update={"observation": data.observation.model_copy(update=changes)})
    with pytest.raises(DomainError) as error:
        record_snapshot(**{**observation, "data": changed})
    assert error.value.status == 409


def test_future_snapshot_cannot_be_attached_to_earlier_event(observation):
    data = observation["data"]
    changed = data.model_copy(
        update={
            "observation": data.observation.model_copy(
                update={"observed_at": data.observation.observed_at + timedelta(days=1)}
            )
        }
    )
    with pytest.raises(DomainError, match="match"):
        record_snapshot(**{**observation, "data": changed})


def test_cross_organization_and_wrong_event_paths_are_rejected(observation):
    with pytest.raises(DomainError) as error:
        record_snapshot(**{**observation, "org_id": uuid4()})
    assert error.value.status == 404
    with pytest.raises(DomainError):
        record_snapshot(**{**observation, "event_id": uuid4()})


@pytest.mark.parametrize("operation", ["update", "delete", "truncate"])
def test_snapshot_history_is_immutable(observation, operation):
    row, _ = record_snapshot(**observation)
    with (
        pytest.raises(DBAPIError, match="append-only"),
        observation["engine"].begin() as connection,
    ):
        if operation == "update":
            connection.execute(
                feature_snapshots.update()
                .where(feature_snapshots.c.id == row["id"])
                .values(dataset_id="b" * 16)
            )
        elif operation == "delete":
            connection.execute(
                feature_snapshots.delete().where(feature_snapshots.c.id == row["id"])
            )
        else:
            connection.execute(sa.text("TRUNCATE observation_features"))


def test_audit_failure_rolls_back_snapshot(observation, monkeypatch):
    from app.features import service

    def fail(*args, **kwargs):
        raise RuntimeError("Injected audit store failure")

    monkeypatch.setattr(service, "append_audit", fail)
    with pytest.raises(RuntimeError, match="Injected"):
        record_snapshot(**observation)
    with pytest.raises(DomainError):
        read_snapshot(**{key: value for key, value in observation.items() if key != "data"})


def test_feature_api_create_replay_and_extra_field_rejection(observation):
    app = create_app(Settings())
    with TestClient(app) as client:
        app.state.engine = observation["engine"]
        path = (
            f"/v1/organizations/{observation['org_id']}/payments/{observation['payment_id']}"
            f"/events/{observation['event_id']}/features"
        )
        body = observation["data"].model_dump(mode="json")
        assert client.post(path, json=body).status_code == 201
        assert client.post(path, json=body).status_code == 200
        assert client.get(path).json()["schema_version"] == "features-v1"
        body["observation"]["true_failure_category"] = "insufficient_funds"
        assert client.post(path, json=body).status_code == 422
