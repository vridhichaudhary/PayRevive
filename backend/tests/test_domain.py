from datetime import UTC, datetime, timedelta

import pytest
from app.domain.state import next_attempt_state
from app.ingestion.schemas import EventInput, PaymentInput
from pydantic import ValidationError


@pytest.mark.parametrize("bad_amount", [0, -1, 1.5, True, "100", 2**63])
def test_amount_requires_positive_integer_minor_units(bad_amount):
    with pytest.raises(ValidationError):
        PaymentInput(reference="subscription", amount_minor=bad_amount)


def test_success_absorbs_newer_failure():
    now = datetime.now(UTC)
    assert next_attempt_state("succeeded", now, "failed", now + timedelta(hours=1)) == (
        "succeeded",
        now,
    )


def test_late_success_is_retained_but_stale_pending_is_not():
    now = datetime.now(UTC)
    earlier = now - timedelta(hours=1)
    assert next_attempt_state("failed", now, "pending", earlier) == ("failed", now)
    assert next_attempt_state("failed", now, "succeeded", earlier) == ("succeeded", earlier)


@pytest.mark.parametrize("extra", [{"card_number": "sensitive"}, {"cvv": "sensitive"}])
def test_sensitive_or_unknown_fields_are_rejected(extra):
    with pytest.raises(ValidationError):
        PaymentInput(reference="subscription", amount_minor=100, **extra)


def test_events_require_timezone_and_no_future_observations():
    base = dict(source_event_id="event", attempt_number=1, status="failed")
    for value in [datetime(2020, 1, 1), datetime.now(UTC) + timedelta(days=1)]:
        with pytest.raises(ValidationError):
            EventInput(**base, occurred_at=value)
