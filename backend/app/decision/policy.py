"""Transparent simulation assumptions. No external provider or legal rules are encoded."""

import hashlib
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt

Positive = Annotated[StrictInt, Field(gt=0)]


class RecoveryRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_total_attempts: Annotated[StrictInt, Field(ge=1, le=10)] = 3
    cooldown_hours: Positive = 6
    funds_delay_hours: Positive = 24
    transient_delay_hours: Positive = 6
    max_age_hours: Positive = 168
    approval_amount_minor: Positive = 100000
    action_cost_minor: Positive = 25
    funds_success_bps: Annotated[StrictInt, Field(ge=0, le=10000)] = 2500
    transient_success_bps: Annotated[StrictInt, Field(ge=0, le=10000)] = 5000

    @property
    def version(self) -> str:
        return "sim-v2-" + hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:16]


class Proposal(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["retry", "request_update", "review", "no_action"]
    reason: str
    due_at: AwareDatetime | None = None
    success_bps: Annotated[StrictInt, Field(ge=0, le=10000)] = 0
    cost_minor: Annotated[StrictInt, Field(ge=0)] = 0


def propose(
    reason: str | None, failed_at: datetime, now: datetime, rules: RecoveryRules
) -> Proposal:
    if reason == "expired_method":
        return Proposal(kind="request_update", reason="expired_method")
    if reason == "risk_block":
        return Proposal(kind="no_action", reason="risk_restriction")
    if reason not in {"insufficient_funds", "issuer_unavailable", "gateway_error"}:
        return Proposal(kind="review", reason="ambiguous_failure")
    funds = reason == "insufficient_funds"
    delay = rules.funds_delay_hours if funds else rules.transient_delay_hours
    return Proposal(
        kind="retry",
        reason=reason,
        due_at=max(now, failed_at + timedelta(hours=max(delay, rules.cooldown_hours))),
        success_bps=rules.funds_success_bps if funds else rules.transient_success_bps,
        cost_minor=rules.action_cost_minor,
    )
