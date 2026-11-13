"""Persistent processor stand-in. No provider client, card data, or real money."""

import hashlib
import json
from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from app.domain.tables import actions, operations
from app.ingestion.service import DomainError, scope


class ProcessorTimeout(TimeoutError):
    pass


def request_hash(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()


class SimulatorGateway:
    absence_is_authoritative = True

    def __init__(self, engine: Engine, mode: Literal["normal", "before", "after"] = "normal"):
        self.engine, self.mode = engine, mode

    def lookup(self, org_id, operation_id):
        with self.engine.connect() as connection:
            return connection.scalar(
                sa.select(operations.c.receipt).where(
                    scope(operations, org_id), operations.c.id == operation_id
                )
            )

    def execute(self, request: dict, now: datetime) -> dict:
        if self.mode == "before":
            raise ProcessorTimeout("Simulated timeout before processor commit")
        operation_id, org_id = request["action_id"], request["org_id"]
        fingerprint = request_hash(request)
        # Actual success assumptions are separate from the policy's conservative estimates.
        draw = int.from_bytes(
            hashlib.sha256(f"simulator-v1:{operation_id}".encode()).digest()[:8], "big"
        )
        probability = 3500 if request["reason"] == "insufficient_funds" else 6500
        succeeded = draw % 10000 < probability
        receipt = {
            "operation_id": str(operation_id),
            "org_id": str(org_id),
            "payment_id": str(request["payment_id"]),
            "request_hash": fingerprint,
            "status": "succeeded" if succeeded else "failed",
            "reason": None if succeeded else request["reason"],
            "amount_minor": request["amount_minor"],
            "cost_minor": request["cost_minor"],
            "occurred_at": now.isoformat(),
            "simulator_version": "simulator-v1",
        }
        with self.engine.begin() as connection:
            existing = (
                connection.execute(
                    sa.select(operations).where(
                        scope(operations, org_id), operations.c.id == operation_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is None:
                state = connection.scalar(
                    sa.select(actions.c.status)
                    .where(
                        scope(actions, org_id),
                        actions.c.id == operation_id,
                        actions.c.payment_id == request["payment_id"],
                    )
                    .with_for_update()
                )
                if state != "executing":
                    raise DomainError(409, "Processor request has no executing authorization")
                connection.execute(
                    insert(operations)
                    .values(
                        id=operation_id,
                        org_id=org_id,
                        payment_id=request["payment_id"],
                        request_hash=fingerprint,
                        receipt=receipt,
                    )
                    .on_conflict_do_nothing(index_elements=[operations.c.id])
                )
            stored = (
                connection.execute(
                    sa.select(operations).where(
                        scope(operations, org_id), operations.c.id == operation_id
                    )
                )
                .mappings()
                .one()
            )
            if stored["request_hash"] != fingerprint:
                raise DomainError(409, "Operation key reused with different request details")
            receipt = stored["receipt"]
        if self.mode == "after":
            raise ProcessorTimeout("Simulated timeout after processor commit")
        return receipt
