# PayRevive

Simulation-first revenue recovery and payment intelligence for subscription card payments.
Initial demonstration: one organization, INR, local execution, no real money.

The intended MVP combines degradation detection, failure diagnosis, SHAP, deterministic
recovery/eligibility, safe execution, auditability, and reproducible monetary experiments.
**Current implementation: foundations, transactional ingestion, and a reproducible synthetic
payment ecosystem with injected incidents.** No recovery execution or trained models exist yet. See [Phase 1](docs/phase-1.md) for the domain and API contract.
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

## Next phases

1. Domain model, ownership, ingestion, audit, outbox: implemented; see Phase 1 verification.
2. Synthetic ecosystem, separate incident truth, reports and replayable ingestion: see Phase 2.
3. Rule-based decisions, independent eligibility, scheduling, reconciliation.
4. Degradation detection, diagnosis, temporal ML evaluation, SHAP.
5. Comparable monetary experiments with seeds, controls, and uncertainty intervals.
6. Dashboard and a documented cold-start demonstration.

Sandbox provider selection is deferred until the simulator is reliable. Bandits and LLMs
are post-MVP. Synthetic performance will not be presented as real-world effectiveness.
