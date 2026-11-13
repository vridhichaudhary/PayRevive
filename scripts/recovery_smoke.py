"""Real API approval/cancellation plus scheduler -> worker -> ledger -> outcome."""

import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

ORG = "00000000-0000-0000-0000-000000000001"


def main() -> None:
    base = f"/v1/organizations/{ORG}"
    with httpx.Client(base_url="http://localhost:8000", timeout=15) as client:

        def post(path, body=None):
            response = client.post(path, json=body) if body is not None else client.post(path)
            response.raise_for_status()
            return response.json()

        def create_payment(customer, amount):
            payment = post(
                base + "/payments",
                {
                    "reference": f"recovery-smoke-{uuid4()}",
                    "amount_minor": amount,
                    "customer_id": customer,
                },
            )
            post(
                base + f"/payments/{payment['id']}/events",
                {
                    "source_event_id": str(uuid4()),
                    "attempt_number": 1,
                    "status": "failed",
                    "reason": "issuer_unavailable",
                    "occurred_at": (datetime.now(UTC) - timedelta(hours=30)).isoformat(),
                },
            )
            return payment["id"]

        customer = post(
            base + "/customers", {"reference": f"smoke-{uuid4()}", "retry_consent": True}
        )
        payment = create_payment(customer["id"], 149900)
        action = post(base + f"/payments/{payment}/recommend")["action"]
        path = base + f"/recovery/actions/{action['id']}"
        assert action["status"] == "awaiting_approval"
        assert post(path + "/execute")["status"] == "awaiting_approval"
        assert post(path + "/approve")["approved_by"] == "local_operator"
        result = post(path + "/execute")
        for _ in range(30):
            if result["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.25)
            result = client.get(path).json()
        assert result["status"] in {"succeeded", "failed"}, result
        post(path + "/execute")
        timeline = client.get(base + f"/payments/{payment}").json()
        assert len(timeline["recovery_outcomes"]) == 1 and len(timeline["attempts"]) == 2
        assert timeline["recovery_outcomes"][0]["cost_minor"] == 25

        pending_payment = create_payment(customer["id"], 149900)
        pending = post(base + f"/payments/{pending_payment}/recommend")["action"]
        response = client.put(
            base + f"/customers/{customer['id']}/consent", json={"retry_consent": False}
        )
        response.raise_for_status()
        pending_path = base + f"/recovery/actions/{pending['id']}"
        assert post(pending_path + "/execute")["status"] == "cancelled"
        assert not client.get(base + f"/payments/{pending_payment}").json()["recovery_outcomes"]

        # No recommend, approve, or execute HTTP calls for this third payment.
        autonomous = post(
            base + "/customers", {"reference": f"auto-{uuid4()}", "retry_consent": True}
        )
        automated_payment = create_payment(autonomous["id"], 99900)
        for _ in range(80):
            response = client.get(base + f"/payments/{automated_payment}")
            response.raise_for_status()
            timeline = response.json()
            if timeline["recovery_outcomes"]:
                break
            time.sleep(0.5)
        assert len(timeline["recovery_outcomes"]) == 1, timeline
        assert len(timeline["attempts"]) == 2
        assert (
            sum(
                action["status"] in {"succeeded", "failed", "executing", "unknown"}
                for action in timeline["recovery_actions"]
            )
            == 1
        )
    print(
        json.dumps(
            {
                "approved_payment": payment,
                "cancelled_payment": pending_payment,
                "autonomous_payment": automated_payment,
            },
            sort_keys=True,
        )
    )
    print("PASS: approval, execution replay, optout, and autonomous scheduler/worker recovery")


if __name__ == "__main__":
    main()
