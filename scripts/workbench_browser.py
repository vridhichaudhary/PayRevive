"""Real browser acceptance for the isolated workbench fixture (Playwright test tooling)."""

import json
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ORG = "00000000-0000-0000-0000-000000000006"
URL = "http://127.0.0.1:8000"
BASE = f"{URL}/v1/organizations/{ORG}"


def main():
    with sync_playwright() as browser:
        chromium = browser.chromium.launch()
        page = chromium.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(f"{URL}/?org={ORG}#incidents")
        page.get_by_role("button", name="Investigate exposed subscriptions").click()
        expect(
            page.get_by_text("Incident issuer-demo · exposure match", exact=False)
        ).to_be_visible()
        page.get_by_role("link", name="workbench-issuer-exposure", exact=True).click()
        expect(page.get_by_text("Awaiting approval", exact=False)).to_be_visible()
        case_url = page.url
        payment_id = case_url.split("#case/")[1]
        before = page.request.get(f"{BASE}/payments/{payment_id}").json()
        action = before["recovery_actions"][0]
        page.on("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Approve simulator retry").click()
        # Replaying approval must reuse the same action. The worker may already have finished.
        for _ in range(2):
            response = page.request.post(f"{BASE}/recovery/actions/{action['id']}/approve")
            assert response.ok and response.json()["id"] == action["id"]
        for _ in range(60):
            result = page.request.get(f"{BASE}/payments/{payment_id}").json()
            if result["recovery_outcomes"]:
                break
            time.sleep(0.5)
        assert len(result["recovery_outcomes"]) == 1, result
        assert len(result["attempts"]) == 2
        assert sum(a["id"] == action["id"] for a in result["recovery_actions"]) == 1
        assert len({a["decision_id"] for a in result["recovery_actions"]}) == len(
            result["recovery_actions"]
        )
        page.reload()
        assert result["recovery_actions"][0]["status"] in ("succeeded", "failed")
        expect(page.get_by_text("Latest action", exact=False)).to_be_visible()
        page.goto(case_url)
        expect(page.get_by_role("heading", name="workbench-issuer-exposure")).to_be_visible()
        Path("work/browser").mkdir(parents=True, exist_ok=True)
        page.screenshot(path="work/browser/workbench-case.png", full_page=True)
        Path("work/browser/result.json").write_text(
            json.dumps(
                {
                    "case_url": case_url,
                    "action_id": action["id"],
                    "outcome": result["recovery_outcomes"][0]["result"],
                    "assertions": (
                        "exposure → case → approve → outcome; duplicate approval reuses action"
                    ),
                },
                indent=2,
            )
        )
        chromium.close()
    print("PASS: workbench browser journey, reload and duplicate approval")


if __name__ == "__main__":
    main()
