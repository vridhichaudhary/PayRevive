"""Verify operational artifacts without opening evaluation truth or rendered reports."""

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal

from app.features.contracts import ObservationFeatures, SnapshotInput
from pydantic import BaseModel, ConfigDict, Field

from simulator.config import GeneratorConfig, month_start
from simulator.generator import dataset_identity
from simulator.models import Attempt, CardMetadata, Customer, Payment

FILES = {
    "config.json",
    "operational/customers.jsonl",
    "operational/methods.jsonl",
    "operational/payments.jsonl",
    "operational/attempts.jsonl",
    "operational/features.jsonl",
    "truth/labels.jsonl",
    "truth/incidents.jsonl",
    "reports/summary.json",
    "reports/incidents.png",
}
SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dataset_id: Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]
    generator_version: str
    python_version: str
    matplotlib_version: str
    numpy_version: str
    synthetic_only: Literal[True]
    model_input_contract: str
    files: dict[str, SHA]


def verified_bytes(directory: Path, manifest: Manifest, name: str) -> bytes:
    if name not in FILES or name not in manifest.files:
        raise ValueError("Unexpected artifact path")
    path = directory / name
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Artifact must stay inside its dataset directory")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != manifest.files[name]:
        raise ValueError(f"Checksum mismatch: {name}")
    return content


def records(directory, manifest, name, model):
    return tuple(
        model.model_validate_json(line)
        for line in verified_bytes(directory, manifest, name).splitlines()
    )


def unique(records_, attribute):
    result = {getattr(row, attribute): row for row in records_}
    if len(result) != len(records_):
        raise ValueError(f"Duplicate {attribute}")
    return result


@dataclass(frozen=True)
class OperationalDataset:
    directory: Path
    manifest: Manifest
    manifest_sha256: str
    config: GeneratorConfig
    customers: tuple[Customer, ...]
    methods: tuple[CardMetadata, ...]
    payments: tuple[Payment, ...]
    attempts: tuple[Attempt, ...]
    features: tuple[ObservationFeatures, ...]

    @cached_property
    def features_by_ref(self):
        return MappingProxyType({f.payment_ref: f for f in self.features})

    def snapshot_input(self, payment_ref: str) -> SnapshotInput:
        feature = self.features_by_ref[payment_ref]
        return SnapshotInput(
            source_dataset_id=self.manifest.dataset_id,
            source_manifest_sha256=self.manifest_sha256,
            generator_version=self.manifest.generator_version,
            observation=feature,
        )


def load_operational(directory: Path) -> OperationalDataset:
    directory = directory.resolve()
    manifest_path = directory / "manifest.json"
    if not manifest_path.resolve().is_relative_to(directory):
        raise ValueError("Manifest must stay inside its dataset directory")
    raw_manifest = manifest_path.read_bytes()
    manifest = Manifest.model_validate_json(raw_manifest)
    if set(manifest.files) != FILES:
        raise ValueError("Manifest file set does not match the dataset contract")
    config = GeneratorConfig.model_validate_json(verified_bytes(directory, manifest, "config.json"))
    if manifest.dataset_id != dataset_identity(config, manifest.generator_version):
        raise ValueError("Dataset identity does not match its producer version/configuration")
    customers = records(directory, manifest, "operational/customers.jsonl", Customer)
    methods = records(directory, manifest, "operational/methods.jsonl", CardMetadata)
    payments = records(directory, manifest, "operational/payments.jsonl", Payment)
    attempts = records(directory, manifest, "operational/attempts.jsonl", Attempt)
    features = records(directory, manifest, "operational/features.jsonl", ObservationFeatures)
    customer_map, method_map = unique(customers, "customer_id"), unique(methods, "method_id")
    payment_map = unique(payments, "payment_ref")
    attempt_map, feature_map = unique(attempts, "payment_ref"), unique(features, "payment_ref")
    unique(attempts, "source_event_id")
    expected = config.customers * config.months
    if (
        len(customers) != config.customers
        or len(methods) != config.customers
        or len(payments) != expected
    ):
        raise ValueError("Population counts do not match configuration")
    if payment_map.keys() != attempt_map.keys() or payment_map.keys() != feature_map.keys():
        raise ValueError("Operational observation keys do not match")
    start = datetime.combine(config.start, datetime.min.time(), UTC)
    end = datetime.combine(month_start(config.start, config.months), datetime.min.time(), UTC)
    if set(Counter(m.customer_id for m in methods).values()) != {1}:
        raise ValueError("Generator v1 requires one method per customer")
    if {p.customer_id for p in payments} != customer_map.keys() or (
        set(Counter(p.customer_id for p in payments).values()) != {config.months}
        or len({(p.customer_id, p.due_at.year, p.due_at.month) for p in payments}) != expected
    ):
        raise ValueError("Monthly customer cohorts do not match configuration")
    history = defaultdict(list)
    for method in methods:
        if method.org_id != config.org_id or method.customer_id not in customer_map:
            raise ValueError("Invalid method ownership")
    for customer in customers:
        if customer.org_id != config.org_id:
            raise ValueError("Invalid customer ownership")
    for payment in sorted(payments, key=lambda row: (row.due_at, row.payment_ref)):
        attempt, feature = attempt_map[payment.payment_ref], feature_map[payment.payment_ref]
        customer, method = customer_map.get(payment.customer_id), method_map.get(payment.method_id)
        if customer is None or method is None or method.customer_id != customer.customer_id:
            raise ValueError("Invalid payment relationship")
        if any(row.org_id != config.org_id for row in (payment, attempt, feature)):
            raise ValueError("Observation organization mismatch")
        if (
            payment.due_at.tzinfo is None
            or not start <= payment.due_at < end
            or payment.due_at != attempt.occurred_at
            or attempt.occurred_at != feature.observed_at
        ):
            raise ValueError("Observation timestamp mismatch")
        if (
            feature.source_event_id != attempt.source_event_id
            or feature.amount_minor != payment.amount_minor
            or feature.segment != customer.segment
            or feature.tenure_months != customer.tenure_months
            or feature.issuer != method.issuer
            or feature.network != method.network
            or feature.gateway != attempt.account
            or feature.days_to_expiry != (method.expires_on - feature.observed_at.date()).days
        ):
            raise ValueError("Feature metadata does not match operational entities")
        if payment.due_at.day != customer.billing_day:
            raise ValueError("Billing day mismatch")
        prior = history[payment.customer_id]
        expected_rate = sum(prior) / len(prior) if prior else None
        if feature.prior_payment_count != len(prior) or feature.prior_success_rate != expected_rate:
            raise ValueError("History feature is not strictly point-in-time")
        if attempt.status != "failed" and attempt.reason is not None:
            raise ValueError("Successful observations cannot have a failure reason")
        prior.append(attempt.status == "succeeded")
    return OperationalDataset(
        directory,
        manifest,
        hashlib.sha256(raw_manifest).hexdigest(),
        config,
        customers,
        methods,
        tuple(sorted(payments, key=lambda row: (row.due_at, row.payment_ref))),
        tuple(
            attempt_map[p.payment_ref]
            for p in sorted(payments, key=lambda r: (r.due_at, r.payment_ref))
        ),
        tuple(
            feature_map[p.payment_ref]
            for p in sorted(payments, key=lambda r: (r.due_at, r.payment_ref))
        ),
    )
