"""Explicit evaluation-only access. Never imported by the serving feature service."""

from dataclasses import dataclass
from datetime import UTC, datetime

from ml.data.loading import OperationalDataset, records, unique
from simulator.config import month_start
from simulator.models import Incident, Label


@dataclass(frozen=True)
class EvaluationTruth:
    labels: tuple[Label, ...]
    incidents: tuple[Incident, ...]


def load_truth(data: OperationalDataset) -> EvaluationTruth:
    labels = records(data.directory, data.manifest, "truth/labels.jsonl", Label)
    incidents = records(data.directory, data.manifest, "truth/incidents.jsonl", Incident)
    by_ref = unique(labels, "payment_ref")
    if by_ref.keys() != {p.payment_ref for p in data.payments}:
        raise ValueError("Evaluation labels do not match operational payment keys")
    unique(incidents, "incident_id")
    incident_ids = {i.incident_id for i in incidents}
    for attempt in data.attempts:
        label = by_ref[attempt.payment_ref]
        if label.source_event_id != attempt.source_event_id or (
            (label.true_failure_category is None) != (attempt.status == "succeeded")
        ):
            raise ValueError("Label alignment/status mismatch")
        if attempt.reason is not None and label.true_failure_category != attempt.reason:
            raise ValueError("Disclosed reason does not match synthetic label")
        if not set(label.exposed_incident_ids) <= incident_ids:
            raise ValueError("Label references unknown incident")
    start = datetime.combine(data.config.start, datetime.min.time(), UTC)
    end = datetime.combine(
        month_start(data.config.start, data.config.months), datetime.min.time(), UTC
    )
    for incident in incidents:
        if (
            incident.starts_at.tzinfo is None
            or incident.ends_at.tzinfo is None
            or not (start <= incident.starts_at < incident.ends_at <= end)
        ):
            raise ValueError("Invalid incident time interval")
    return EvaluationTruth(tuple(by_ref[p.payment_ref] for p in data.payments), incidents)
