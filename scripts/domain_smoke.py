"""Exercise real HTTP ingestion and inspect durable effects in PostgreSQL."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import sqlalchemy as sa
from app.config import get_settings
from app.db import build_engine
from app.domain.tables import DEMO_ORG_ID, audit, events, outbox


def verify_ingestion() -> None:
    path = f"/v1/organizations/{DEMO_ORG_ID}/payments"
    with httpx.Client(base_url="http://localhost:8000", timeout=15) as client:
        response = client.post(path, json={"reference": f"smoke-{uuid4()}", "amount_minor": 149900})
        assert response.status_code == 201, response.text
        payment_id = response.json()["id"]
        path += f"/{payment_id}"
        event = {
            "source_event_id": str(uuid4()),
            "attempt_number": 1,
            "status": "failed",
            "reason": "insufficient_funds",
            "occurred_at": (datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
        }
        assert client.post(path + "/events", json=event).status_code == 201
        assert client.post(path + "/events", json=event).status_code == 200
        assert client.post(path + "/events", json={**event, "reason": "unknown"}).status_code == 409
        success = {
            **event,
            "source_event_id": str(uuid4()),
            "attempt_number": 2,
            "status": "succeeded",
            "reason": None,
            "occurred_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        }
        assert client.post(path + "/events", json=success).status_code == 201
        assert (
            client.post(
                path + "/events", json={**event, "source_event_id": str(uuid4())}
            ).status_code
            == 201
        )
        response = client.get(path)
        response.raise_for_status()
        result = response.json()
        assert result["status"] == "succeeded"
        assert len(result["attempts"]) == 2
        assert len(result["events"]) == 3
        assert len(result["audit"]) == 4
    engine = build_engine(get_settings())
    try:
        with engine.connect() as connection:
            for table, expected in [(events, 3), (audit, 4)]:
                count = connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(table)
                    .where(table.c.org_id == DEMO_ORG_ID, table.c.payment_id == payment_id)
                )
                assert count == expected
            count = connection.scalar(
                sa.select(sa.func.count())
                .select_from(
                    outbox.join(
                        events,
                        sa.and_(
                            outbox.c.org_id == events.c.org_id, outbox.c.event_id == events.c.id
                        ),
                    )
                )
                .where(
                    events.c.org_id == DEMO_ORG_ID,
                    events.c.payment_id == payment_id,
                    outbox.c.published_at.is_(None),
                )
            )
            assert count == 3
    finally:
        engine.dispose()
    print(f"PASS: HTTP lifecycle, idempotency, success preservation, audit/outbox ({payment_id})")


if __name__ == "__main__":
    verify_ingestion()
