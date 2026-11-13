"""One feature contract shared by generation, analysis, and future inference."""

from datetime import UTC
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator

FEATURE_SCHEMA_VERSION = "features-v1"
Category = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")]

# Explanations describe candidate inputs, not a claim of measured predictive utility.
FEATURE_RATIONALES = {
    "amount_minor": "Amount known before execution; integer paise, not floating-point money.",
    "segment": "Synthetic customer segment known before execution; inspect proxy effects.",
    "tenure_months": "Baseline customer tenure at population start; not updated monthly in v1.",
    "issuer": "Safe method metadata; may help localize degradation, not establish causality.",
    "network": "Method metadata correlated with issuer; assess redundant information later.",
    "gateway": "Routing metadata known at execution; inspect gateway-specific degradation.",
    "days_to_expiry": "Expiry metadata relative to observation time; noisy in this simulator.",
    "day_of_month": "Calendar context; hypothesized funds-cycle signal, not a fact about people.",
    "hour_utc": "UTC observation hour; not the customer's local hour.",
    "gateway_latency_ms": "Post-response latency; suitable for diagnosis, not preflight.",
    "prior_payment_count": "Count of strictly earlier initial outcomes; excludes this payment.",
    "prior_success_rate": "Rate of earlier initial outcomes; null for empty history.",
}
MODEL_INPUT_FIELDS = tuple(FEATURE_RATIONALES)


class FeatureValues(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    amount_minor: Annotated[StrictInt, Field(gt=0, le=2**63 - 1)]
    segment: Category
    tenure_months: Annotated[StrictInt, Field(ge=0)]
    issuer: Category
    network: Category
    gateway: Category
    days_to_expiry: StrictInt
    day_of_month: Annotated[StrictInt, Field(ge=1, le=31)]
    hour_utc: Annotated[StrictInt, Field(ge=0, le=23)]
    gateway_latency_ms: Annotated[StrictInt, Field(gt=0)]
    prior_payment_count: Annotated[StrictInt, Field(ge=0)]
    prior_success_rate: Annotated[float | None, Field(ge=0, le=1, allow_inf_nan=False, strict=True)]

    @model_validator(mode="after")
    def consistent_history(self):
        if (self.prior_payment_count == 0) != (self.prior_success_rate is None):
            raise ValueError("History rate must be null exactly when history count is zero")
        return self

    def model_inputs(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in MODEL_INPUT_FIELDS}


class ObservationFeatures(FeatureValues):
    # Join metadata never enters model_inputs().
    payment_ref: Identifier
    source_event_id: Identifier
    org_id: UUID
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def calendar_alignment(self):
        observed = self.observed_at.astimezone(UTC)
        object.__setattr__(self, "observed_at", observed)
        if (observed.day, observed.hour) != (self.day_of_month, self.hour_utc):
            raise ValueError("Calendar features must match the UTC observation timestamp")
        return self


class SnapshotInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["features-v1"] = FEATURE_SCHEMA_VERSION
    source_dataset_id: Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]
    source_manifest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    generator_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$", max_length=20)]
    observation: ObservationFeatures
