# PayRevive

Simulation-first revenue recovery and payment intelligence for subscription card payments.
Initial demonstration: one organization, INR, local execution, no real money.

The intended MVP combines degradation detection, failure diagnosis, SHAP, deterministic
recovery/eligibility, safe execution, auditability, and reproducible monetary experiments.
**Current implementation: foundations, transactional ingestion, a reproducible synthetic
ecosystem, and consent-gated rule-based recovery through a persistent execution simulator.**
No trained models or incremental-revenue evaluation exist yet. See [Phase 1](docs/phase-1.md) for the domain and API contract.
See [Phase 0 verification](docs/phase-0.md) and [architecture](docs/architecture.md).

## Run locally

Prerequisites: a running Docker engine and Docker Compose v2+, with port 8000 available.
From the repository root:

```sh
cp .env.example .env
docker compose up --build --wait --wait-timeout 180
docker compose exec -T api python scripts/smoke.py
```

Open http://localhost:8000/docs for OpenAPI. `/health/live` reports process liveness;
`/health/ready` requires PostgreSQL, the expected migration, and Redis.
Only the API port is published, bound to loopback. The credentials are local-demo defaults.
Choose a different `API_PORT` in `.env` if necessary. Passwords containing reserved URL
characters need URL encoding in the database URL; use the supplied defaults for this demo.

Compose starts PostgreSQL and Redis, applies Alembic migrations, then starts API and worker.
No manual SQL or separate migration command is needed for initial startup.

```sh
docker compose ps
docker compose logs api worker migrate
docker compose run --rm migrate
docker compose down
```

`down` preserves named database/Redis volumes. `down --volumes` destroys this project's
local data: use it only when intentionally resetting the demonstration.
Do not run multiple independent migration processes concurrently.

## Develop and test

Install uv and Python 3.13, then:

```sh
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest -q -W error
docker compose --profile test run --build --rm test-runner
uv run alembic -c backend/alembic.ini upgrade head --sql
docker compose config --quiet
```

Python dependencies are pinned in `uv.lock`. Docker installs the frozen runtime dependencies;
unit tests use the development group. The integration smoke test deliberately uses a real
worker, not Celery eager mode. CI runs both unit and container integration checks, then
repeats migration and startup against retained volumes.

If uv's default cache is inaccessible, prefix uv commands with `UV_CACHE_DIR=/tmp/payrevive-uv-cache`.
`.env` supplies Compose interpolation; direct Python execution reads the shell's
`PAYREVIVE_*` environment variables, not `.env` automatically.

## Generate synthetic data

```sh
uv run python -m simulator.run --config simulator/configs/baseline.json --output work/datasets/demo
uv run python -m simulator.ingest work/datasets/demo --limit 50
```

Start the API before ingestion. Repeat ingestion to check idempotency. Export paths must be
new. Open `work/datasets/demo/reports/incidents.png` to inspect injected degradation.
Read [the Phase 2 guide](docs/phase-2.md) and [dataset card](docs/dataset_card.md) for assumptions,
operational/truth boundaries, reproducibility, and limitations. Generated files are not committed.

## Try safe recovery

```sh
docker compose exec -T api python scripts/recovery_smoke.py
```

The API exposes online customer consent, recommendations, approvals, action state, and
reconciliation. Existing unlinked payments are not automatically granted retry permission.
See [Phase 3](docs/phase-3.md) for the state machine, assumptions, examples, and safety tests.
Compose now also runs one Celery Beat scheduler; PostgreSQL owns all due times and state.

## Data science and notebooks

```sh
uv sync --frozen --group analysis
uv run --group analysis python -m ml.notebooks.run --dataset work/datasets/demo --output work/analysis/data-audit
```

Generate the dataset first using the command above. Open `work/analysis/data-audit/report.html`
without Jupyter, or use [the notebook instructions](notebooks/README.md) for interactive work.
The notebook imports tested modules; there is no duplicate ML implementation. Read
[Phase 4A](docs/phase-4a.md) and [actual audit findings](docs/experiment_reports/data-audit-v1.md).
Snapshots can be imported with `simulator.ingest ... --with-features`.

[Phase 4B](docs/phase-4b.md) adds daily issuer and gateway degradation detection,
a fixed-threshold comparison, and an executable notebook with synthetic incident metrics.
[Phase 4C](docs/phase-4c.md) adds offline per-payment diagnosis, temporal
model comparison, abstention, and a SHAP notebook.

## Next phases

1. Domain model, ownership, ingestion, audit, outbox: implemented; see Phase 1 verification.
2. Synthetic ecosystem, separate incident truth, reports and replayable ingestion: see Phase 2.
3. Consent-gated rule decisions, eligibility, scheduling and reconciliation: see Phase 3.
4. Degradation detection, diagnosis, temporal ML evaluation, SHAP: implemented as offline replays.
5. [Matched monetary experiments](docs/phase-5.md): implemented as reproducible simulation runs.
6. Dashboard and a documented cold-start demonstration.

Sandbox provider selection is deferred until the simulator is reliable. Bandits and LLMs
are post-MVP. Synthetic performance will not be presented as real-world effectiveness.
