from collections import Counter
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from simulator.config import GeneratorConfig
from simulator.generator import canonical, generate
from simulator.models import Features


def test_identical_configuration_is_reproducible_and_seed_changes_population():
    config = GeneratorConfig(customers=50)
    a, b = generate(config), generate(config)
    assert canonical(a.model_dump(mode="json")) == canonical(b.model_dump(mode="json"))
    assert a != generate(config.model_copy(update={"seed": 43}))


def test_entities_are_linked_owned_and_recurring():
    config = GeneratorConfig(customers=80)
    data = generate(config)
    customers = {c.customer_id: c for c in data.customers}
    methods = {m.method_id: m for m in data.methods}
    assert len(data.payments) == len(data.attempts) == len(data.features) == len(data.labels) == 240
    assert len({p.payment_ref for p in data.payments}) == 240
    assert len({a.source_event_id for a in data.attempts}) == 240
    for payment, attempt, feature, label in zip(
        data.payments, data.attempts, data.features, data.labels, strict=True
    ):
        customer, method = customers[payment.customer_id], methods[payment.method_id]
        assert payment.org_id == method.org_id == customer.org_id == config.org_id
        assert method.customer_id == payment.customer_id
        assert payment.due_at.day == customer.billing_day
        assert payment.amount_minor in [
            customer.recurring_amount_minor,
            2 * customer.recurring_amount_minor,
        ]
        assert (
            payment.payment_ref == attempt.payment_ref == feature.payment_ref == label.payment_ref
        )
        assert attempt.occurred_at < datetime.now(UTC)
        assert (attempt.status == "failed") == (label.true_failure_category is not None)


def test_feature_history_uses_strictly_earlier_observations():
    data = generate(GeneratorConfig(customers=60))
    history = {}
    for payment, attempt, feature in zip(data.payments, data.attempts, data.features, strict=True):
        prior = history.setdefault(payment.customer_id, [])
        assert feature.prior_payment_count == len(prior)
        assert feature.prior_success_rate == (sum(prior) / len(prior) if prior else None)
        prior.append(attempt.status == "succeeded")


def test_features_reject_labels_and_future_outcomes():
    feature = generate(GeneratorConfig(customers=30)).features[0].model_dump()
    for key in [
        "true_failure_category",
        "exposed_incident_ids",
        "recovery_outcome",
        "reason",
        "status",
    ]:
        with pytest.raises(ValidationError):
            Features(**feature, **{key: "leak"})


@pytest.mark.parametrize("seed", [7, 42, 99])
def test_paired_incidents_raise_failure_rates_on_same_cohorts(seed):
    config = GeneratorConfig(customers=6000, seed=seed)
    incident, baseline = (
        generate(config),
        generate(config.model_copy(update={"incident_strength": 0})),
    )
    assert [p.due_at for p in incident.payments] == [p.due_at for p in baseline.payments]
    assert [p.amount_minor for p in incident.payments] == [
        p.amount_minor for p in baseline.payments
    ]
    assert [f.issuer for f in incident.features] == [f.issuer for f in baseline.features]
    for specification in incident.incidents:
        indices = [
            i
            for i, label in enumerate(incident.labels)
            if specification.incident_id in label.exposed_incident_ids
        ]
        assert len(indices) >= 60
        rate = sum(incident.attempts[i].status == "failed" for i in indices) / len(indices)
        control = sum(baseline.attempts[i].status == "failed" for i in indices) / len(indices)
        assert rate - control > 0.15
    # No perfect outcome rules: even an expired method overlaps with success in this simulation.
    expired = [
        a.status
        for a, f in zip(incident.attempts, incident.features, strict=True)
        if f.days_to_expiry < 0
    ]
    assert set(expired) == {"failed", "succeeded"}
    categories = Counter(label.true_failure_category for label in incident.labels)
    assert all(
        categories[key] > 10
        for key in [
            "insufficient_funds",
            "expired_method",
            "issuer_unavailable",
            "gateway_error",
            "risk_block",
            "unknown",
        ]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"start": "2026-01-02"},
        {"months": 1},
        {"seed": True},
        {"customers": 0},
        {"incident_strength": float("nan")},
        {"start": "2099-01-01"},
    ],
)
def test_invalid_or_future_configuration_is_rejected(changes):
    with pytest.raises(ValidationError):
        GeneratorConfig(**changes)


def test_model_input_allowlist_excludes_identifiers_and_time_keys():
    row = generate(GeneratorConfig(customers=30)).features[0]
    assert (
        not {"payment_ref", "source_event_id", "org_id", "observed_at"} & row.model_inputs().keys()
    )


def test_reason_disclosure_does_not_change_hidden_outcomes():
    hidden = generate(GeneratorConfig(customers=80, reason_disclosure_rate=0))
    disclosed = generate(GeneratorConfig(customers=80, reason_disclosure_rate=1))
    assert [label.true_failure_category for label in hidden.labels] == [
        label.true_failure_category for label in disclosed.labels
    ]
    assert all(attempt.reason is None for attempt in hidden.attempts)
    assert all(
        attempt.reason is not None for attempt in disclosed.attempts if attempt.status == "failed"
    )
