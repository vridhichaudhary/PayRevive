from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaymentInput(InputModel):
    reference: Identifier
    amount_minor: Annotated[StrictInt, Field(gt=0, le=9223372036854775807)]
    currency: Literal["INR"] = "INR"
    customer_id: UUID | None = None


class EventInput(InputModel):
    source_event_id: Identifier
    provider: Literal["simulator"] = "simulator"
    account: Identifier = "demo"
    attempt_number: Annotated[StrictInt, Field(gt=0, le=2147483647)]
    status: Literal["pending", "failed", "succeeded"]
    occurred_at: AwareDatetime
    reason: (
        Literal[
            "insufficient_funds",
            "expired_method",
            "issuer_unavailable",
            "gateway_error",
            "risk_block",
            "unknown",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_observation(self):
        if self.account == "payrevive_recovery" or self.source_event_id.startswith("rcv_"):
            raise ValueError("Recovery observation identities are reserved")
        self.occurred_at = self.occurred_at.astimezone(UTC)
        if self.status != "failed" and self.reason is not None:
            raise ValueError("Only failed events can include a failure reason")
        if self.occurred_at > datetime.now(UTC):
            raise ValueError("An observation cannot occur in the future")
        return self
