from datetime import date, datetime
from typing import Literal
from uuid import UUID

from app.features.contracts import MODEL_INPUT_FIELDS as MODEL_INPUT_FIELDS
from app.features.contracts import ObservationFeatures as Features
from pydantic import BaseModel, ConfigDict

Reason = Literal[
    "insufficient_funds",
    "expired_method",
    "issuer_unavailable",
    "gateway_error",
    "risk_block",
    "unknown",
]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Customer(Record):
    customer_id: str
    org_id: UUID
    segment: Literal["budget", "standard", "premium"]
    tenure_months: int
    billing_day: int
    recurring_amount_minor: int


class CardMetadata(Record):
    method_id: str
    org_id: UUID
    customer_id: str
    type: Literal["card"] = "card"
    issuer: str
    network: str
    expires_on: date
    simulator_ref: str


class Payment(Record):
    payment_ref: str
    org_id: UUID
    customer_id: str
    method_id: str
    amount_minor: int
    currency: Literal["INR"] = "INR"
    due_at: datetime


class Attempt(Record):
    payment_ref: str
    org_id: UUID
    source_event_id: str
    attempt_number: Literal[1] = 1
    provider: Literal["simulator"] = "simulator"
    account: str
    status: Literal["failed", "succeeded"]
    reason: Reason | None
    occurred_at: datetime


class Incident(Record):
    incident_id: str
    scope: Literal["issuer", "gateway"]
    target: str
    starts_at: datetime
    ends_at: datetime
    added_failure_hazard: float


class Label(Record):
    payment_ref: str
    source_event_id: str
    true_failure_category: Reason | None
    exposed_incident_ids: tuple[str, ...]


class Dataset(Record):
    dataset_id: str
    customers: tuple[Customer, ...]
    methods: tuple[CardMetadata, ...]
    payments: tuple[Payment, ...]
    attempts: tuple[Attempt, ...]
    features: tuple[Features, ...]
    labels: tuple[Label, ...]
    incidents: tuple[Incident, ...]
