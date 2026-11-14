"""Matched recovery replays with integer paise accounting."""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Annotated, Literal

from app.compliance.engine import EligibilityContext, assess
from app.decision.policy import RecoveryRules, propose
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from simulator.config import GeneratorConfig
from simulator.generator import generate, random_stream
from simulator.models import Reason

Policy = Literal["control", "fixed", "reason_based"]
POLICIES: tuple[Policy, ...] = ("control", "fixed", "reason_based")
EXPERIMENT_VERSION = "recovery-replay-v1"


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    first_seed: Annotated[StrictInt, Field(ge=0, le=2**32 - 501)] = 1000
    runs: Annotated[StrictInt, Field(ge=2, le=500)] = 100
    customers: Annotated[StrictInt, Field(ge=30, le=6000)] = 500
    horizon_hours: Annotated[StrictInt, Field(ge=1, le=168)] = 168
    fixed_delay_hours: Annotated[StrictInt, Field(ge=1, le=168)] = 48
    consent_rate: Annotated[float, Field(ge=0, le=1)] = 0.85
    approval_rate: Annotated[float, Field(ge=0, le=1)] = 0.75
    background_rate: Annotated[float, Field(ge=0, le=1)] = 0.15
    readiness_time_scale: Annotated[float, Field(gt=0, le=10)] = 1.0
    bootstrap_draws: Annotated[StrictInt, Field(ge=100, le=20000)] = 4000
    bootstrap_seed: Annotated[StrictInt, Field(ge=0)] = 721
    rules: RecoveryRules = RecoveryRules()


@dataclass(frozen=True)
class Case:
    amount_minor: int
    failed_at: datetime
    reason: Reason | None
    consent: bool
    approved: bool
    background_at: datetime | None
    ready_at: datetime | None


@dataclass(frozen=True)
class Outcome:
    settled: bool
    via_retry: bool = False
    retries: int = 0
    stop: str = "no_intervention"


def replay(case: Case, policy: Policy, config: ExperimentConfig) -> Outcome:
    deadline = case.failed_at + timedelta(hours=config.horizon_hours)
    background = case.background_at is not None and case.background_at <= deadline
    if policy == "control":
        return Outcome(background)
    rules = config.rules
    context = EligibilityContext(
        "failed",
        case.amount_minor,
        True,
        case.consent,
        1,
        case.failed_at,
        case.failed_at,
        failure_reason=case.reason,
        terminal_evidence=case.reason in ("expired_method", "risk_block"),
    )
    retries = 0
    while True:
        # Both policies start from the shared proposal and differ only in retry timing.
        proposal = propose(case.reason, context.last_attempt_at, context.last_attempt_at, rules)
        eligibility = assess(context, proposal, rules, context.last_attempt_at)
        if not eligibility.allowed:
            return Outcome(background, retries=retries, stop=eligibility.reason)
        assert proposal.due_at is not None
        due = proposal.due_at
        if policy == "fixed":
            due = max(due, context.last_attempt_at + timedelta(hours=config.fixed_delay_hours))
            proposal = proposal.model_copy(update={"due_at": due})
        if due > deadline:
            return Outcome(background, retries=retries, stop="horizon")
        if case.background_at is not None and case.background_at <= due:
            return Outcome(True, retries=retries, stop="background_settlement")
        eligibility = assess(context, proposal, rules, due, executing=True, approved=case.approved)
        if not eligibility.allowed:
            return Outcome(background, retries=retries, stop=eligibility.reason)
        retries += 1
        if case.ready_at is not None and case.ready_at <= due:
            return Outcome(True, True, retries, "retry_settlement")
        context = replace(context, attempt_number=context.attempt_number + 1, last_attempt_at=due)


def build_cases(seed: int, config: ExperimentConfig) -> tuple[str, list[Case]]:
    data = generate(GeneratorConfig(seed=seed, customers=config.customers))
    payments = {p.payment_ref: p for p in data.payments}
    labels = {label.payment_ref: label.true_failure_category for label in data.labels}
    cases = []
    for attempt in data.attempts:
        if attempt.status != "failed":
            continue
        payment = payments[attempt.payment_ref]
        reason = labels[attempt.payment_ref]
        # Streams are keyed by entity and mechanism, never by policy or execution order.
        rng = random_stream(seed, f"{EXPERIMENT_VERSION}:customer:{payment.customer_id}")
        consent = rng.random() < config.consent_rate
        approved = rng.random() < config.approval_rate
        rng = random_stream(seed, f"{EXPERIMENT_VERSION}:settlement:{payment.payment_ref}")
        background_at = (
            attempt.occurred_at + timedelta(hours=rng.uniform(1, 168))
            if rng.random() < config.background_rate
            else None
        )
        rng = random_stream(seed, f"{EXPERIMENT_VERSION}:readiness:{payment.payment_ref}")
        # Shared latent readiness: time until a retry would succeed, if ever.
        ready_at = None
        if reason == "insufficient_funds" and rng.random() < 0.60:
            ready_at = attempt.occurred_at + timedelta(
                hours=rng.uniform(3, 96) * config.readiness_time_scale
            )
        if reason in ("issuer_unavailable", "gateway_error") and rng.random() < 0.85:
            ready_at = attempt.occurred_at + timedelta(
                hours=rng.uniform(0.5, 30) * config.readiness_time_scale
            )
        cases.append(
            Case(
                payment.amount_minor,
                attempt.occurred_at,
                attempt.reason,
                consent,
                approved,
                background_at,
                ready_at,
            )
        )
    return data.dataset_id, cases


def compare(cases: list[Case], config: ExperimentConfig) -> dict:
    at_risk = sum(case.amount_minor for case in cases)
    control = [replay(case, "control", config) for case in cases]
    control_amount = sum(
        case.amount_minor for case, result in zip(cases, control, strict=True) if result.settled
    )
    result = {}
    for policy in POLICIES:
        totals = Counter()
        stops = Counter()
        for case, baseline in zip(cases, control, strict=True):
            outcome = baseline if policy == "control" else replay(case, policy, config)
            totals["settled"] += outcome.settled
            totals["gross_minor"] += case.amount_minor if outcome.settled else 0
            totals["retry_recovered_minor"] += case.amount_minor if outcome.via_retry else 0
            totals["retry_recovered_also_in_control_minor"] += (
                case.amount_minor if outcome.via_retry and baseline.settled else 0
            )
            totals["retried_payments"] += outcome.retries > 0
            totals["retried_without_incremental_settlement"] += outcome.retries > 0 and (
                baseline.settled or not outcome.settled
            )
            totals["retries"] += outcome.retries
            stops[outcome.stop] += 1
        gross = totals["gross_minor"]
        incremental = gross - control_amount
        cost = totals["retries"] * config.rules.action_cost_minor
        result[policy] = {
            **dict(totals),
            "at_risk_minor": at_risk,
            "failed_payments": len(cases),
            "incremental_minor": incremental,
            "amount_recovery_rate": gross / at_risk if at_risk else None,
            "non_incremental_retry_payment_rate": (
                totals["retried_without_incremental_settlement"] / totals["retried_payments"]
                if totals["retried_payments"]
                else None
            ),
            "cost_minor": cost,
            "net_incremental_minor": incremental - cost,
            "stops": dict(stops),
        }
    return result
