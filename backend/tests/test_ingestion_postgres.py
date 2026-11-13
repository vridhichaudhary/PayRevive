"""Real PostgreSQL tests. Run using the isolated Compose test profile."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.config import Settings
from app.domain.tables import attempts, audit, events, metadata, organizations, outbox, payments
from app.ingestion import service
from app.ingestion.schemas import EventInput, PaymentInput
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.postgres


@pytest.fixture
def payment(engine, org):
    return service.create_payment(
        engine, org, PaymentInput(reference=str(uuid4()), amount_minor=149900)
    )[0]["id"]


def observation(**changes):
    return EventInput(
        **{
            **dict(
                source_event_id=str(uuid4()),
                attempt_number=1,
                status="failed",
                occurred_at=datetime.now(UTC) - timedelta(minutes=5),
                reason="insufficient_funds",
            ),
            **changes,
        }
    )


def counts(engine, org):
    with engine.connect() as connection:
        return {
            table.name: connection.scalar(
                sa.select(sa.func.count()).select_from(table).where(table.c.org_id == org)
            )
            for table in [payments, attempts, events, audit, outbox]
        }


def test_concurrent_duplicate_deliveries_have_one_logical_effect(engine, org, payment):
    data = observation()
    barrier = Barrier(6)

    def deliver(_):
        barrier.wait()
        return service.ingest_event(engine, org, payment, data)

    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(deliver, range(6)))
    assert sum(not item["duplicate"] for item in results) == 1
    assert len({item["event_id"] for item in results}) == 1
    assert counts(engine, org) == {
        "payments": 1,
        "payment_attempts": 1,
        "payment_events": 1,
        "audit_events": 2,
        "outbox_messages": 1,
    }


def test_conflicting_duplicate_is_rejected_without_extra_writes(engine, org, payment):
    data = observation()
    service.ingest_event(engine, org, payment, data)
    before = counts(engine, org)
    with pytest.raises(service.DomainError) as error:
        service.ingest_event(engine, org, payment, data.model_copy(update={"reason": "unknown"}))
    assert error.value.status == 409
    assert counts(engine, org) == before


def test_equivalent_timezone_duplicate(engine, org, payment):
    data = observation(occurred_at="2026-01-01T00:00:00Z")
    service.ingest_event(engine, org, payment, data)
    equivalent = observation(
        source_event_id=data.source_event_id, occurred_at="2026-01-01T05:30:00+05:30"
    )
    assert service.ingest_event(engine, org, payment, equivalent)["duplicate"]


def test_late_failures_never_regress_success(engine, org, payment):
    now = datetime.now(UTC) - timedelta(minutes=1)
    service.ingest_event(
        engine, org, payment, observation(status="succeeded", reason=None, occurred_at=now)
    )
    service.ingest_event(engine, org, payment, observation(occurred_at=now - timedelta(minutes=1)))
    service.ingest_event(
        engine, org, payment, observation(attempt_number=2, occurred_at=now + timedelta(seconds=1))
    )
    result = service.get_payment(engine, org, payment)
    assert result["status"] == "succeeded"
    assert result["attempts"][0]["status"] == "succeeded"
    assert len(result["events"]) == 3


def test_older_attempt_failure_does_not_override_latest_pending_attempt(engine, org, payment):
    service.ingest_event(
        engine, org, payment, observation(attempt_number=2, status="pending", reason=None)
    )
    service.ingest_event(engine, org, payment, observation())
    assert service.get_payment(engine, org, payment)["status"] == "pending"


def test_failed_outbox_write_rolls_back_every_ingestion_effect(engine, org, payment):
    before = counts(engine, org)

    def fail_outbox(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO outbox_messages"):
            raise RuntimeError("Injected outbox storage failure")

    sa.event.listen(engine, "before_cursor_execute", fail_outbox)
    try:
        with pytest.raises(RuntimeError, match="Injected"):
            service.ingest_event(engine, org, payment, observation())
    finally:
        sa.event.remove(engine, "before_cursor_execute", fail_outbox)
    assert counts(engine, org) == before
    assert service.get_payment(engine, org, payment)["status"] == "pending"


@pytest.mark.parametrize("table", [events, audit])
@pytest.mark.parametrize("operation", ["update", "delete", "truncate"])
def test_history_is_immutable_at_database_level(engine, org, payment, table, operation):
    service.ingest_event(engine, org, payment, observation())
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
        if operation == "update":
            connection.execute(table.update().where(table.c.org_id == org).values(id=uuid4()))
        elif operation == "delete":
            connection.execute(table.delete().where(table.c.org_id == org))
        else:
            connection.execute(sa.text(f"TRUNCATE {table.name} CASCADE"))


def test_organization_filters_and_composite_foreign_keys(engine, org, payment):
    other = uuid4()
    with engine.begin() as connection:
        connection.execute(organizations.insert().values(id=other, name="Other"))
    with pytest.raises(service.DomainError) as error:
        service.get_payment(engine, other, payment)
    assert error.value.status == 404
    with pytest.raises(service.DomainError):
        service.ingest_event(engine, other, payment, observation())
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            attempts.insert().values(
                id=uuid4(),
                org_id=other,
                payment_id=payment,
                number=1,
                status="failed",
                state_at=datetime.now(UTC),
            )
        )


def test_source_identity_is_scoped_by_org_and_account(engine, org, payment):
    data = observation(source_event_id="same_id")
    service.ingest_event(engine, org, payment, data)
    service.ingest_event(engine, org, payment, data.model_copy(update={"account": "other"}))
    other = uuid4()
    with engine.begin() as connection:
        connection.execute(organizations.insert().values(id=other, name="Other"))
    other_payment = service.create_payment(
        engine, other, PaymentInput(reference="subscription", amount_minor=100)
    )[0]["id"]
    assert not service.ingest_event(engine, other, other_payment, data)["duplicate"]
    assert counts(engine, org)["payment_events"] == 2


def test_payment_reference_is_idempotent_but_cannot_change_amount(engine, org):
    data = PaymentInput(reference="subscription", amount_minor=100)
    first, duplicate = service.create_payment(engine, org, data)
    assert not duplicate
    second, duplicate = service.create_payment(engine, org, data)
    assert duplicate and first["id"] == second["id"]
    with pytest.raises(service.DomainError):
        service.create_payment(engine, org, data.model_copy(update={"amount_minor": 200}))
    assert counts(engine, org)["audit_events"] == 1


def test_api_create_ingest_replay_conflict_and_timeline(engine, org):
    app = create_app(Settings())
    with TestClient(app) as client:
        app.state.engine = engine
        path = f"/v1/organizations/{org}/payments"
        response = client.post(path, json={"reference": "api", "amount_minor": 99900})
        assert response.status_code == 201
        payment_id = response.json()["id"]
        event_path = f"{path}/{payment_id}/events"
        data = observation().model_dump(mode="json")
        assert client.post(event_path, json=data).status_code == 201
        assert client.post(event_path, json=data).status_code == 200
        assert client.post(event_path, json={**data, "reason": "unknown"}).status_code == 409
        assert client.post(event_path, json={**data, "cvv": "sensitive"}).status_code == 422
        result = client.get(f"{path}/{payment_id}")
        assert result.status_code == 200
        assert result.json()["status"] == "failed"
        assert len(result.json()["events"]) == 1
        assert client.get(f"/v1/organizations/{uuid4()}/payments/{payment_id}").status_code == 404


def test_migrated_schema_matches_domain_metadata(engine):
    def include_object(obj, name, type_, reflected, compare_to):
        return name not in {"alembic_version"}

    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"include_object": include_object})
        assert compare_metadata(context, metadata) == []


def test_concurrent_distinct_events_on_same_payment_preserve_success(engine, org, payment):
    barrier = Barrier(2)
    observations = [observation(), observation(status="succeeded", reason=None)]

    def deliver(data):
        barrier.wait()
        return service.ingest_event(engine, org, payment, data)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(deliver, observations))
    assert all(not result["duplicate"] for result in results)
    result = service.get_payment(engine, org, payment)
    assert result["status"] == "succeeded"
    assert result["attempts"][0]["status"] == "succeeded"
    assert counts(engine, org)["outbox_messages"] == 2


def test_concurrent_event_identity_cannot_attach_to_two_payments(engine, org, payment):
    other = service.create_payment(
        engine, org, PaymentInput(reference="another", amount_minor=500)
    )[0]["id"]
    barrier = Barrier(2)
    data = observation()

    def deliver(payment_id):
        barrier.wait()
        try:
            service.ingest_event(engine, org, payment_id, data)
            return 201
        except service.DomainError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(deliver, [payment, other])) == [201, 409]
    result = counts(engine, org)
    assert result["payment_events"] == result["payment_attempts"] == 1
    assert result["audit_events"] == 3
