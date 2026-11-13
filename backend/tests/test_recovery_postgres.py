"""Safety properties against real PostgreSQL, including durable interruption points."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa
from app.config import Settings
from app.decision.policy import RecoveryRules
from app.domain.tables import (
    actions,
    attempts,
    decisions,
    events,
    operations,
    outbox,
    outcomes,
    preferences,
)
from app.execution.simulator import SimulatorGateway
from app.ingestion.schemas import EventInput, PaymentInput
from app.ingestion.service import DomainError, create_payment, ingest_event
from app.main import create_app
from app.recovery import execution, planning
from app.recovery.customers import create_customer, set_consent
from app.recovery.dispatch import consume_event, dispatch
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.postgres


@pytest.fixture
def case(engine, org):
    def factory(
        amount=99900, reason="issuer_unavailable", consent=True, hours_ago=30, number=1, linked=True
    ):
        now = datetime.now(UTC)
        customer = create_customer(engine, org, str(uuid4()), consent)[0]
        payment = create_payment(
            engine,
            org,
            PaymentInput(
                reference=str(uuid4()),
                amount_minor=amount,
                customer_id=customer["id"] if linked else None,
            ),
        )[0]
        event = ingest_event(
            engine,
            org,
            payment["id"],
            EventInput(
                source_event_id=str(uuid4()),
                status="failed",
                reason=reason,
                attempt_number=number,
                occurred_at=now - timedelta(hours=hours_ago),
            ),
        )
        return dict(
            engine=engine,
            org=org,
            customer=customer["id"],
            payment=payment["id"],
            now=now,
            event=event["event_id"],
        )

    return factory


def plan(case):
    return planning.recommend(case["engine"], case["org"], case["payment"], case["now"])


def count(case, table):
    with case["engine"].connect() as connection:
        return connection.scalar(
            sa.select(sa.func.count()).select_from(table).where(table.c.org_id == case["org"])
        )


def execute(case, action, **kwargs):
    return execution.execute(case["engine"], case["org"], action["id"], now=case["now"], **kwargs)


def test_concurrent_recommendations_create_one_decision_and_action(case):
    c = case()
    barrier = Barrier(4)

    def call(_):
        barrier.wait()
        return plan(c)["action"]["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(call, range(4)))) == 1
    assert count(c, decisions) == count(c, actions) == 1


@pytest.mark.parametrize(
    "reason,consent,linked,expected",
    [
        (None, True, True, "not_an_executable_action"),
        ("unknown", True, True, "not_an_executable_action"),
        ("risk_block", True, True, "not_an_executable_action"),
        ("expired_method", True, True, "not_an_executable_action"),
        ("issuer_unavailable", False, True, "missing_consent"),
        ("issuer_unavailable", True, False, "missing_consent"),
    ],
)
def test_noneligible_evidence_never_schedules(case, reason, consent, linked, expected):
    c = case(reason=reason, consent=consent, linked=linked)
    result = plan(c)
    assert result["action"] is None
    assert result["decision"]["eligibility"]["reason"] == expected
    assert count(c, operations) == 0


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"amount": 1}, "uneconomic"),
        ({"hours_ago": 200}, "recovery_window_expired"),
        ({"number": 3}, "attempt_limit"),
    ],
)
def test_age_attempt_and_economic_limits_block_actions(case, changes, reason):
    c = case(**changes)
    assert plan(c)["decision"]["eligibility"]["reason"] == reason
    assert count(c, actions) == 0


def test_approval_is_required_and_does_not_execute_or_bypass_due_time(case):
    c = case(amount=149900, hours_ago=1)
    action = plan(c)["action"]
    assert action["status"] == "awaiting_approval"
    assert execute(c, action)["status"] == "awaiting_approval"
    approved = execution.approve(c["engine"], c["org"], action["id"], c["now"])
    assert approved["status"] == "scheduled"
    assert execute(c, action)["status"] == "scheduled"
    assert count(c, operations) == 0
    result = execution.execute(
        c["engine"], c["org"], action["id"], now=c["now"] + timedelta(hours=6)
    )
    assert result["status"] in {"succeeded", "failed"}
    assert count(c, operations) == count(c, outcomes) == 1


def test_optout_cancels_scheduled_work_and_create_replay_does_not_restore_consent(case):
    c = case()
    action = plan(c)["action"]
    customer = set_consent(c["engine"], c["org"], c["customer"], False)
    assert customer["revision"] == 2
    assert execute(c, action)["status"] == "cancelled"
    replay, duplicate = create_customer(c["engine"], c["org"], customer["reference"], True)
    assert duplicate and not replay["retry_consent"]
    assert count(c, preferences) == 2
    assert count(c, operations) == 0


def test_success_before_execution_supersedes_retry(case):
    c = case()
    action = plan(c)["action"]
    ingest_event(
        c["engine"],
        c["org"],
        c["payment"],
        EventInput(
            source_event_id=str(uuid4()),
            status="succeeded",
            attempt_number=1,
            occurred_at=c["now"] - timedelta(minutes=1),
        ),
    )
    assert execute(c, action)["status"] == "superseded"
    assert count(c, operations) == 0


def test_approval_is_invalidated_by_new_evidence_or_policy(case, monkeypatch):
    c = case(amount=149900)
    action = plan(c)["action"]
    execution.approve(c["engine"], c["org"], action["id"], c["now"])
    ingest_event(
        c["engine"],
        c["org"],
        c["payment"],
        EventInput(
            source_event_id=str(uuid4()),
            status="failed",
            reason="gateway_error",
            attempt_number=1,
            occurred_at=c["now"] - timedelta(minutes=1),
        ),
    )
    assert execute(c, action)["status"] == "superseded"
    c2 = case()
    action2 = plan(c2)["action"]
    monkeypatch.setattr(planning, "current_rules", lambda: RecoveryRules(cooldown_hours=12))
    assert execute(c2, action2)["status"] == "superseded"
    assert count(c2, operations) == 0


def test_missing_compliance_rules_never_calls_processor(case, monkeypatch):
    c = case()
    action = plan(c)["action"]
    monkeypatch.setattr(planning, "current_rules", lambda: None)
    with pytest.raises(DomainError) as error:
        execute(c, action)
    assert error.value.status == 503
    assert execution.read_action(c["engine"], c["org"], action["id"])["status"] == "scheduled"
    assert count(c, operations) == 0


def test_concurrent_execution_and_replay_have_one_processor_operation(case):
    c = case()
    action = plan(c)["action"]
    barrier = Barrier(6)

    def call(_):
        barrier.wait()
        return execute(c, action)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(call, range(6)))
    assert execute(c, action)["status"] in {"succeeded", "failed"}
    assert count(c, operations) == count(c, outcomes) == 1
    assert count(c, attempts) == count(c, events) == 2


@pytest.mark.parametrize("mode", ["before", "after"])
def test_timeouts_reconcile_same_operation_without_allocating_another_attempt(case, mode):
    c = case()
    action = plan(c)["action"]
    result = execute(c, action, gateway=SimulatorGateway(c["engine"], mode=mode))
    assert result["status"] == "unknown" and result["attempt_number"] == 2
    assert count(c, outcomes) == 0 and count(c, attempts) == 1
    assert count(c, operations) == (1 if mode == "after" else 0)
    result = execution.reconcile(c["engine"], c["org"], action["id"], now=c["now"])
    assert result["status"] in {"succeeded", "failed"}
    execution.reconcile(c["engine"], c["org"], action["id"], now=c["now"])
    assert count(c, operations) == count(c, outcomes) == 1
    assert count(c, attempts) == 2


def test_worker_crash_after_claim_is_resumed_only_after_lease(case):
    c = case()
    action = plan(c)["action"]
    request = execution._claim(c["engine"], c["org"], action["id"], c["now"])
    assert request["attempt_number"] == 2
    result = execution.reconcile(c["engine"], c["org"], action["id"], now=c["now"])
    assert result["status"] == "executing" and count(c, operations) == 0
    result = execution.reconcile(
        c["engine"], c["org"], action["id"], now=c["now"] + timedelta(seconds=61)
    )
    assert result["status"] in {"succeeded", "failed"}
    assert count(c, operations) == count(c, outcomes) == 1


def test_worker_crash_after_processor_commit_is_reconciled_without_resubmission(case):
    class CrashGateway(SimulatorGateway):
        def execute(self, request, now):
            super().execute(request, now)
            raise SystemExit("Injected process loss after processor commit")

    c = case()
    action = plan(c)["action"]
    with pytest.raises(SystemExit):
        execute(c, action, gateway=CrashGateway(c["engine"]))
    assert count(c, operations) == 1 and count(c, outcomes) == 0
    result = execution.reconcile(c["engine"], c["org"], action["id"], now=c["now"])
    assert result["status"] in {"succeeded", "failed"}
    assert count(c, outcomes) == 1


def test_outcome_storage_failure_rolls_back_projection_but_preserves_receipt(case):
    c = case()
    action = plan(c)["action"]

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO recovery_outcomes"):
            raise RuntimeError("Injected outcome store failure")

    sa.event.listen(c["engine"], "before_cursor_execute", fail)
    try:
        assert execute(c, action)["status"] == "unknown"
    finally:
        sa.event.remove(c["engine"], "before_cursor_execute", fail)
    assert count(c, operations) == 1 and count(c, outcomes) == 0
    assert count(c, attempts) == count(c, events) == 1
    execution.reconcile(c["engine"], c["org"], action["id"], now=c["now"])
    assert count(c, outcomes) == 1 and count(c, attempts) == 2


def test_reserved_attempt_number_rejects_external_observations(case):
    c = case()
    action = plan(c)["action"]
    execute(c, action, gateway=SimulatorGateway(c["engine"], mode="before"))
    with pytest.raises(DomainError) as error:
        ingest_event(
            c["engine"],
            c["org"],
            c["payment"],
            EventInput(
                source_event_id=str(uuid4()),
                status="failed",
                reason="unknown",
                attempt_number=2,
                occurred_at=c["now"] - timedelta(minutes=1),
            ),
        )
    assert error.value.status == 409


def test_unknown_outcome_is_recorded_after_optout_but_no_absent_operation_is_retried(case):
    for mode in ["before", "after"]:
        c = case()
        action = plan(c)["action"]
        execute(c, action, gateway=SimulatorGateway(c["engine"], mode=mode))
        set_consent(c["engine"], c["org"], c["customer"], False)
        result = execution.reconcile(c["engine"], c["org"], action["id"], now=c["now"])
        assert result["status"] in ({"succeeded", "failed"} if mode == "after" else {"cancelled"})
        with c["engine"].connect() as connection:
            n = connection.scalar(
                sa.select(sa.func.count())
                .select_from(outcomes)
                .where(outcomes.c.action_id == action["id"])
            )
        assert n == (1 if mode == "after" else 0)


def test_non_authoritative_absence_never_resubmits_and_eventually_requires_review(case):
    class UncertainGateway(SimulatorGateway):
        absence_is_authoritative = False

    c = case()
    action = plan(c)["action"]
    execute(c, action, gateway=SimulatorGateway(c["engine"], mode="before"))
    for _ in range(5):
        result = execution.reconcile(
            c["engine"], c["org"], action["id"], gateway=UncertainGateway(c["engine"]), now=c["now"]
        )
    assert result["status"] == "unknown" and result["requires_review"]
    assert result["next_reconcile_at"] is None
    assert count(c, operations) == 0
    assert plan(c)["action"]["id"] == action["id"]


def test_outbox_retries_publication_until_consumed_and_rolls_back_broker_failure(case):
    c = case()

    def fail(*args):
        raise ConnectionError("Injected broker outage")

    with pytest.raises(ConnectionError):
        dispatch(c["engine"], fail, c["now"], org_id=c["org"])
    with c["engine"].connect() as connection:
        assert (
            connection.scalar(
                sa.select(outbox.c.published_at).where(outbox.c.event_id == c["event"])
            )
            is None
        )
    published = []

    def publisher(name, args):
        published.append((name, args))

    dispatch(c["engine"], publisher, c["now"], org_id=c["org"])
    dispatch(c["engine"], publisher, c["now"] + timedelta(seconds=31), org_id=c["org"])
    assert len(published) == 2
    consume_event(c["engine"], c["org"], c["event"])
    consume_event(c["engine"], c["org"], c["event"])
    assert count(c, decisions) == count(c, actions) == 1
    published.clear()
    dispatch(c["engine"], publisher, c["now"] + timedelta(seconds=62), org_id=c["org"])
    assert not any(name == "payrevive.recovery_event" for name, _ in published)


def test_recovery_api_consent_approval_and_scoped_access(engine, org):
    app = create_app(Settings())
    with TestClient(app) as client:
        app.state.engine = engine
        base = f"/v1/organizations/{org}"
        response = client.post(
            base + "/customers", json={"reference": "api", "retry_consent": True}
        )
        assert response.status_code == 201
        customer = response.json()["id"]
        payment = client.post(
            base + "/payments",
            json={"reference": "api", "amount_minor": 149900, "customer_id": customer},
        ).json()["id"]
        assert (
            client.post(
                base + f"/payments/{payment}/events",
                json={
                    "source_event_id": "api",
                    "status": "failed",
                    "reason": "gateway_error",
                    "attempt_number": 1,
                    "occurred_at": (datetime.now(UTC) - timedelta(hours=30)).isoformat(),
                },
            ).status_code
            == 201
        )
        result = client.post(base + f"/payments/{payment}/recommend")
        assert result.status_code == 200
        action = result.json()["action"]["id"]
        assert (
            client.post(base + f"/recovery/actions/{action}/approve").json()["status"]
            == "scheduled"
        )
        assert (
            client.put(
                base + f"/customers/{customer}/consent", json={"retry_consent": False}
            ).status_code
            == 200
        )
        assert (
            client.post(base + f"/recovery/actions/{action}/execute").json()["status"]
            == "cancelled"
        )
        assert (
            client.get(f"/v1/organizations/{uuid4()}/recovery/actions/{action}").status_code == 404
        )
        assert client.get(base + f"/customers/{customer}").json()["retry_consent"] is False


@pytest.mark.parametrize("table", [decisions, operations, outcomes, preferences])
def test_recovery_history_cannot_be_edited(engine, case, table):
    c = case()
    action = plan(c)["action"]
    execute(c, action)
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
        connection.execute(table.delete().where(table.c.org_id == c["org"]))


def test_stale_absence_lookup_cannot_discard_a_committed_receipt_after_optout(case):
    class StaleLookup(SimulatorGateway):
        def lookup(self, org_id, operation_id):
            return None

    c = case()
    action = plan(c)["action"]
    execute(c, action, gateway=SimulatorGateway(c["engine"], mode="after"))
    set_consent(c["engine"], c["org"], c["customer"], False)
    result = execution.reconcile(
        c["engine"], c["org"], action["id"], gateway=StaleLookup(c["engine"]), now=c["now"]
    )
    assert result["status"] in {"succeeded", "failed"}
    assert count(c, outcomes) == count(c, operations) == 1


def test_operation_key_rejects_changed_request_details(case):
    c = case()
    action = plan(c)["action"]
    request = execution._claim(c["engine"], c["org"], action["id"], c["now"])
    gateway = SimulatorGateway(c["engine"])
    receipt = gateway.execute(request, c["now"])
    assert gateway.execute(request, c["now"] + timedelta(seconds=1)) == receipt
    with pytest.raises(DomainError) as error:
        gateway.execute({**request, "amount_minor": request["amount_minor"] + 1}, c["now"])
    assert error.value.status == 409
    execution.reconcile(c["engine"], c["org"], action["id"], now=c["now"])
    assert count(c, operations) == count(c, outcomes) == 1


def test_conflicting_receipt_does_not_change_financial_projection(case):
    class BadReceipt(SimulatorGateway):
        def execute(self, request, now):
            receipt = super().execute(request, now)
            return {**receipt, "amount_minor": receipt["amount_minor"] + 1}

    c = case()
    action = plan(c)["action"]
    assert execute(c, action, gateway=BadReceipt(c["engine"]))["status"] == "unknown"
    assert count(c, outcomes) == 0 and count(c, attempts) == 1
    execution.reconcile(c["engine"], c["org"], action["id"], now=c["now"])
    assert count(c, outcomes) == 1


def test_two_dispatchers_skip_locked_messages_without_publishing_twice(case):
    from threading import Event

    c = case()
    entered, release = Event(), Event()
    published = []

    def publisher(name, args):
        published.append((name, args))
        entered.set()
        assert release.wait(timeout=3)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(dispatch, c["engine"], publisher, c["now"], org_id=c["org"])
        try:
            assert entered.wait(timeout=2)
            second = pool.submit(dispatch, c["engine"], publisher, c["now"], org_id=c["org"])
            assert second.result(timeout=2) == 0
        finally:
            release.set()
        assert first.result(timeout=2) == 1
    assert len(published) == 1


def test_older_terminal_evidence_still_vetoes_a_previously_allowed_retry(case):
    c = case()
    action = plan(c)["action"]
    ingest_event(
        c["engine"],
        c["org"],
        c["payment"],
        EventInput(
            source_event_id=str(uuid4()),
            status="failed",
            reason="risk_block",
            attempt_number=1,
            occurred_at=c["now"] - timedelta(hours=31),
        ),
    )
    assert execute(c, action)["status"] == "cancelled"
    assert count(c, operations) == 0


def test_customer_link_and_recovery_operations_cannot_cross_organizations(case):
    from app.domain.tables import organizations, payments
    from sqlalchemy.exc import IntegrityError

    c = case()
    action = plan(c)["action"]
    other = uuid4()
    with c["engine"].begin() as connection:
        connection.execute(organizations.insert().values(id=other, name="Other"))
    other_customer = create_customer(c["engine"], other, "other", True)[0]
    with pytest.raises(DomainError):
        create_payment(
            c["engine"],
            other,
            PaymentInput(reference="cross-org", amount_minor=100, customer_id=c["customer"]),
        )
    with pytest.raises(DomainError):
        set_consent(c["engine"], other, c["customer"], False)
    for operation in [execution.execute, execution.approve, execution.reconcile]:
        with pytest.raises(DomainError) as error:
            operation(c["engine"], other, action["id"])
        assert error.value.status == 404
    with pytest.raises(IntegrityError), c["engine"].begin() as connection:
        connection.execute(
            payments.update()
            .where(payments.c.id == c["payment"])
            .values(customer_id=other_customer["id"])
        )
    assert count(c, operations) == 0


def test_database_rejects_different_active_actions_for_one_payment(case):
    from sqlalchemy.exc import IntegrityError

    c = case()
    result = plan(c)
    second_decision = {**result["decision"], "id": uuid4(), "context_hash": "f" * 64}
    second_action = {**result["action"], "id": uuid4(), "decision_id": second_decision["id"]}
    with (
        pytest.raises(IntegrityError, match="uq_active_recovery_payment"),
        c["engine"].begin() as connection,
    ):
        connection.execute(decisions.insert().values(**second_decision))
        connection.execute(actions.insert().values(**second_action))
    assert count(c, decisions) == count(c, actions) == 1
