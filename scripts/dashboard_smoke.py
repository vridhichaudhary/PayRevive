"""Verify the seeded dashboard over real HTTP inside the API container."""

import httpx

from scripts.demo_seed import ORG


def main():
    with httpx.Client(base_url="http://localhost:8000", timeout=15) as client:

        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response

        assert "Payment overview" in get("/").text
        assert "function timeline" in get("/dashboard/assets/dashboard.js").text
        base = f"/v1/organizations/{ORG}"
        snapshot = get(f"{base}/dashboard?status=failed&limit=1").json()
        assert snapshot["summary"]["failed"]["count"] > 0
        assert snapshot["payments"][0]["status"] == "failed"
        pending = next(
            row
            for row in snapshot["pending_approvals"]
            if row["reference"] == "dashboard-demo-approval"
        )
        timeline = get(f"{base}/payments/{pending['payment_id']}").json()
        assert timeline["events"] and timeline["audit"]
        assert timeline["recovery_actions"][0]["status"] == "awaiting_approval"
        assert get("/dashboard/reports/detection").json()["evaluation"]["incident_count"] == 3
        assert get("/dashboard/reports/experiment").json()["config"]["runs"] == 30
    print("PASS: dashboard assets, scoped totals, timeline, pending approval and replay reports")


if __name__ == "__main__":
    main()
