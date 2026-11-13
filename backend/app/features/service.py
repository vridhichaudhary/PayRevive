import hashlib
import json
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from app.domain.tables import attempts, events, feature_snapshots, payments
from app.features.contracts import SnapshotInput
from app.ingestion.service import DomainError, append_audit, scope


def record_snapshot(
    engine: Engine, org_id: UUID, payment_id: UUID, event_id: UUID, data: SnapshotInput
) -> tuple[dict, bool]:
    canonical = json.dumps(
        {"payment_id": str(payment_id), "event_id": str(event_id), **data.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    observed = data.observation
    with engine.begin() as connection:
        payment = (
            connection.execute(
                sa.select(payments)
                .where(scope(payments, org_id), payments.c.id == payment_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if payment is None:
            raise DomainError(404, "Payment not found in organization")
        event = (
            connection.execute(
                sa.select(events).where(
                    scope(events, org_id),
                    events.c.payment_id == payment_id,
                    events.c.id == event_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if event is None:
            raise DomainError(404, "Event not found in payment and organization")
        number = connection.scalar(
            sa.select(attempts.c.number).where(
                scope(attempts, org_id), attempts.c.id == event["attempt_id"]
            )
        )
        if number != 1 or event["status"] not in {"failed", "succeeded"}:
            raise DomainError(409, "Feature v1 supports initial terminal observations only")
        if (
            observed.org_id != org_id
            or observed.payment_ref != payment["reference"]
            or observed.source_event_id != event["source_event_id"]
            or observed.amount_minor != payment["amount_minor"]
            or observed.observed_at != event["occurred_at"]
            or observed.gateway != event["account"]
        ):
            raise DomainError(
                409, "Feature observation does not match immutable payment/event evidence"
            )
        created = connection.execute(
            insert(feature_snapshots)
            .values(
                id=uuid4(),
                org_id=org_id,
                payment_id=payment_id,
                event_id=event_id,
                observed_at=observed.observed_at,
                schema_version=data.schema_version,
                dataset_id=data.source_dataset_id,
                source_manifest_sha256=data.source_manifest_sha256,
                generator_version=data.generator_version,
                fingerprint=fingerprint,
                values=observed.model_dump(mode="json"),
            )
            .on_conflict_do_nothing(
                index_elements=[feature_snapshots.c.org_id, feature_snapshots.c.event_id]
            )
            .returning(feature_snapshots.c.id)
        ).scalar_one_or_none()
        row = (
            connection.execute(
                sa.select(feature_snapshots).where(
                    scope(feature_snapshots, org_id), feature_snapshots.c.event_id == event_id
                )
            )
            .mappings()
            .one()
        )
        if row["fingerprint"] != fingerprint:
            raise DomainError(409, "Feature observation already has different values or provenance")
        if created:
            append_audit(
                connection,
                org_id,
                payment_id,
                "features.recorded",
                {
                    "snapshot_id": str(created),
                    "event_id": str(event_id),
                    "schema_version": data.schema_version,
                    "dataset_id": data.source_dataset_id,
                    "source_manifest_sha256": data.source_manifest_sha256,
                },
            )
        return dict(row), created is None


def read_snapshot(engine: Engine, org_id: UUID, payment_id: UUID, event_id: UUID) -> dict:
    with engine.connect() as connection:
        row = (
            connection.execute(
                sa.select(feature_snapshots).where(
                    scope(feature_snapshots, org_id),
                    feature_snapshots.c.payment_id == payment_id,
                    feature_snapshots.c.event_id == event_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DomainError(404, "Feature snapshot not found in payment and organization")
        return dict(row)
