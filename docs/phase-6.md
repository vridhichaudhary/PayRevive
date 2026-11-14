# Phase 6 — Local dashboard and cold start

The dashboard is served by the existing FastAPI process at
[http://localhost:8000/](http://localhost:8000/). It uses browser-native
HTML, CSS and JavaScript and fetches data from the same origin. PostgreSQL
owns payment state and audit history. The recovery worker still owns execution.

## Cold-start walkthrough

From a fresh checkout with Docker Desktop running:

```sh
cp .env.example .env
docker compose up --build --wait --wait-timeout 180
docker compose exec -T api python -m scripts.demo_seed
docker compose exec -T api python -m scripts.dashboard_smoke
```

Open the dashboard. Filter payments by status and select a reference to see
attempts, events, feature provenance, decisions, outcomes, and the audit
trail. The overview shows totals for the demo organization across all
payment statuses, independently of the current filter or page. Failed
amount means the current amount in failed state. It includes obligations
that may be ineligible for recovery.

The seed imports 65 verified generated payments and adds an explicitly
consenting customer with one simulator payment that needs approval under
the default rules. It also generates the detection and monetary experiment
reports. Run the smoke check before approving that action. Approve
`dashboard-demo-approval`, then use Refresh and open its timeline after
the scheduler and worker apply the simulator result. The simulator can
succeed or fail. Approval uses the existing action endpoint and all
execution-time consent, cooldown, and eligibility checks still apply.

Repeated seed runs reuse payment and event identities, replay feature
provenance, and preserve the existing approval history. Seed once per
fresh demo stack. Report files live in the `demo_reports` volume. Ordinary
`docker compose down` and startup retain data and reports.

## Optional diagnosis and SHAP panel

Generate this report on the host with the locked analysis dependencies.
Copy the trusted local JSON into the API's report volume.

```sh
uv sync --frozen --group analysis
uv run --group analysis python -m simulator.run --config simulator/configs/baseline.json --output work/dashboard-dataset
uv run --group analysis python -m ml.analysis.diagnosis --dataset work/dashboard-dataset --output work/dashboard-diagnosis
docker compose exec -T api sh -c 'mkdir -p "$PAYREVIVE_DEMO_REPORTS/diagnosis" && cat > "$PAYREVIVE_DEMO_REPORTS/diagnosis/.pending" && mv "$PAYREVIVE_DEMO_REPORTS/diagnosis/.pending" "$PAYREVIVE_DEMO_REPORTS/diagnosis/diagnosis.json"' < work/dashboard-diagnosis/diagnosis.json
```

Refresh the dashboard to view the temporal score, abstention coverage and
the largest SHAP contributions for one example. All replay panels label the
generated run context. They summarize offline artifacts and have a clear
empty state until a report is present. The detection panel shows replay
incidents, and the experiment panel shows cohort-level paired contrasts
and uncertainty. Neither panel affects the transaction queue.

## Boundaries

The browser dashboard targets the explicit demo organization. The overview
endpoint scopes every query by organization and reads totals and rows in a
consistent snapshot. It uses bounded pages and reads pending approvals
through a scoped payment/action join. Event and audit details reuse the
existing payment detail endpoint. HTML values from the API are rendered as
text. The app remains a loopback simulator with local operator approvals.
External deployment would require authentication and authorization.
