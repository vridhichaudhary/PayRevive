"""Pure deterministic eligibility; recommendations have no authority to execute."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.decision.policy import Proposal, RecoveryRules


@dataclass(frozen=True)
class EligibilityContext:
    payment_status: str
    amount_minor: int
    customer_present: bool
    consent: bool
    attempt_number: int
    last_attempt_at: datetime
    first_attempt_at: datetime
    failure_reason: str | None = None
    terminal_evidence: bool = False
    other_active_action: bool = False


@dataclass(frozen=True)
class Eligibility:
    allowed: bool
    reason: str
    approval_required: bool = False


def assess(
    context: EligibilityContext,
    proposal: Proposal,
    rules: RecoveryRules | None,
    now: datetime,
    *,
    executing: bool = False,
    approved: bool = False,
) -> Eligibility:
    if rules is None:
        return Eligibility(False, "eligibility_unavailable")
    if proposal.kind != "retry":
        return Eligibility(False, "not_an_executable_action")
    if context.payment_status != "failed":
        return Eligibility(False, "payment_not_failed")
    if not context.customer_present or not context.consent:
        return Eligibility(False, "missing_consent")
    if context.terminal_evidence:
        return Eligibility(False, "terminal_evidence")
    if context.other_active_action:
        return Eligibility(False, "active_action_exists")
    if context.attempt_number >= rules.max_total_attempts:
        return Eligibility(False, "attempt_limit")
    if now - context.first_attempt_at > timedelta(hours=rules.max_age_hours):
        return Eligibility(False, "recovery_window_expired")
    # The proposer cannot supply its own authority, probability estimate, cost, or reason.
    if (
        context.failure_reason not in {"insufficient_funds", "issuer_unavailable", "gateway_error"}
        or proposal.reason != context.failure_reason
    ):
        return Eligibility(False, "unverified_reason")
    funds = context.failure_reason == "insufficient_funds"
    success_bps = rules.funds_success_bps if funds else rules.transient_success_bps
    if proposal.cost_minor != rules.action_cost_minor or proposal.success_bps != success_bps:
        return Eligibility(False, "untrusted_economics")
    # Basis-point arithmetic keeps currency comparisons exact; no float money.
    if context.amount_minor * success_bps <= rules.action_cost_minor * 10000:
        return Eligibility(False, "uneconomic")
    approval_required = context.amount_minor >= rules.approval_amount_minor
    if executing:
        if (
            proposal.due_at is None
            or now < proposal.due_at
            or (
                now
                < context.last_attempt_at
                + timedelta(
                    hours=max(
                        rules.cooldown_hours,
                        rules.funds_delay_hours if funds else rules.transient_delay_hours,
                    )
                )
            )
        ):
            return Eligibility(False, "not_due", approval_required)
        if approval_required and not approved:
            return Eligibility(False, "approval_required", True)
    return Eligibility(True, "eligible", approval_required)
