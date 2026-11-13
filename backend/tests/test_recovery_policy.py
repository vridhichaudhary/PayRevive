from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from app.compliance.engine import EligibilityContext, assess
from app.decision.policy import RecoveryRules, propose
from pydantic import ValidationError

NOW = datetime(2026, 10, 1, tzinfo=UTC)
RULES = RecoveryRules()
CONTEXT = EligibilityContext(
    payment_status="failed",
    amount_minor=99900,
    customer_present=True,
    consent=True,
    attempt_number=1,
    last_attempt_at=NOW - timedelta(hours=30),
    first_attempt_at=NOW - timedelta(hours=30),
)


@pytest.mark.parametrize(
    "reason,kind",
    [
        ("insufficient_funds", "retry"),
        ("issuer_unavailable", "retry"),
        ("gateway_error", "retry"),
        ("expired_method", "request_update"),
        ("risk_block", "no_action"),
        ("unknown", "review"),
        (None, "review"),
    ],
)
def test_policy_maps_known_evidence_and_abstains_on_ambiguity(reason, kind):
    assert propose(reason, CONTEXT.last_attempt_at, NOW, RULES).kind == kind


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"consent": False}, "missing_consent"),
        ({"customer_present": False}, "missing_consent"),
        ({"payment_status": "succeeded"}, "payment_not_failed"),
        ({"payment_status": "pending"}, "payment_not_failed"),
        ({"terminal_evidence": True}, "terminal_evidence"),
        ({"other_active_action": True}, "active_action_exists"),
        ({"attempt_number": 3}, "attempt_limit"),
        ({"amount_minor": 1}, "uneconomic"),
        ({"first_attempt_at": NOW - timedelta(hours=169)}, "recovery_window_expired"),
    ],
)
def test_eligibility_vetoes_independently_of_retry_recommendation(changes, reason):
    proposal = propose("issuer_unavailable", CONTEXT.last_attempt_at, NOW, RULES)
    result = assess(replace(CONTEXT, **changes), proposal, RULES, NOW)
    assert not result.allowed and result.reason == reason


def test_missing_eligibility_rules_fails_closed():
    proposal = propose("issuer_unavailable", CONTEXT.last_attempt_at, NOW, RULES)
    assert assess(CONTEXT, proposal, None, NOW).reason == "eligibility_unavailable"


def test_scheduling_does_not_bypass_execution_time_cooldown_or_approval():
    recent = replace(CONTEXT, amount_minor=149900, last_attempt_at=NOW - timedelta(hours=1))
    proposal = propose("insufficient_funds", recent.last_attempt_at, NOW, RULES)
    assert assess(recent, proposal, RULES, NOW).allowed
    assert assess(recent, proposal, RULES, NOW, executing=True, approved=True).reason == "not_due"
    later = NOW + timedelta(hours=24)
    assert assess(recent, proposal, RULES, later, executing=True).reason == "approval_required"
    assert assess(recent, proposal, RULES, later, executing=True, approved=True).allowed


def test_rule_version_changes_and_invalid_config_cannot_be_loaded():
    assert RecoveryRules().version == RULES.version
    assert RecoveryRules(cooldown_hours=12).version != RULES.version
    with pytest.raises(ValidationError):
        RecoveryRules(max_total_attempts=0)
