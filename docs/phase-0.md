# Phase 0 — Foundations

Status: COMPLETE for local Phase 0 acceptance, verified 13 November 2026. Phase 1 has not started.

## Objective and acceptance gates

Create a reproducible environment for the approved payment-intelligence MVP. Phase 0 does
not implement payment business logic. Do not proceed to Phase 1 until every required local
acceptance gate passes, or explicitly report that the phase remains incomplete.

| Gate | Verification |
| --- | --- |
| Reproducible Python environment | `uv sync --frozen` |
| Clean lint and formatting | `uv run ruff check .`; `uv run ruff format --check .` |
| Settings and health behavior | `uv run pytest -q -W error` |
| Valid service configuration | `docker compose config --quiet` |
| Container build and healthy startup | `docker compose up --build --wait --wait-timeout 180` |
| Actual PostgreSQL migration | Alembic revision and metadata verified by smoke test |
| Actual worker execution | `docker compose exec -T api python scripts/smoke.py` |
| Repeatable migrations and retained-volume startup | rerun migrate; down/up without deleting volumes; rerun smoke |

CI implements the same core checks. Local checks do not establish that a GitHub-hosted
workflow has run; the workflow must execute after publication to verify its hosted result.

## Files and responsibilities

- `pyproject.toml`, `uv.lock`, `.python-version`: dependency graph, exact resolved versions,
  Python version, tooling. A lock file prevents two clones silently using different libraries.
- `backend/app/config.py`: typed environment variables, URL validation, simulation-only mode.
- `backend/app/main.py`, `health.py`: API lifecycle and liveness/readiness contracts.
- `backend/app/db.py`: connection pooling and timeouts; reused by API, migration, and probe.
- `backend/alembic.ini`, `backend/alembic/`: reversible, versioned schema transition.
- `backend/app/worker.py`, `tasks.py`: asynchronous processing wiring and database-backed probe.
- `Dockerfile`, `compose.yaml`: non-root application image, internal service network,
  persistent volumes, health checks, ordered migration/startup.
- `backend/tests/test_foundation.py`: behavior tests independent of a local database.
- `scripts/smoke.py`: real API/database/broker/worker integration test.
- `.github/workflows/ci.yml`: verification on pushes and pull requests.
- `.env.example`, `.gitignore`, `.dockerignore`: local configuration and exclusion of secrets/caches.
- `AGENTS.md`, `README.md`, `docs/architecture.md`: approved expectations, setup, concepts,
  future boundaries, tradeoffs, and interview questions.

## Important concepts

**API versus worker:** HTTP requests should finish promptly. A worker performs queued work
outside the request. The unique-token probe proves the worker executed a particular task;
a broker PING alone cannot prove that.

**Connection pool:** SQLAlchemy reuses database connections instead of opening one for every
request. Connection and statement timeouts limit how long an unavailable dependency can
hold a request. They are per-operation bounds, not an exact whole-request deadline.

**Migration service:** healthy PostgreSQL does not mean that application tables exist.
Compose waits for PostgreSQL readiness, runs Alembic, and starts consumers only after success.
A migration failure blocks those consumers; it is not silently ignored.

**Simulation marker:** the first migration creates only global application metadata.
It validates the migration path without prematurely implementing the Phase 1 domain.
The marker is a consistency check, not a security boundary against database administrators.

**Dependency inversion:** health routes depend on a small probe interface. Unit tests inject
controlled dependency states; the smoke test uses real services. Mocked tests and real
integration checks answer different questions and both are necessary.

**UTC versus business currency:** infrastructure timestamps use UTC. INR monetary values
will be integer minor units when domain models arrive. A future UI can display local time.

**Multi-tenancy:** there is no tenant-owned business data yet. The approved contract requires
explicit organization ownership when those tables and tasks arrive in Phase 1; a generic
base class now would imply isolation that has not actually been implemented or tested.

**Why no ML dependencies yet?** Phase 0 verifies infrastructure only. Detection, diagnosis,
SHAP, and rigorous experiments retain dedicated MVP phases. Installing those libraries now
would increase the image without providing verified functionality.

## Results

Verified on macOS/Apple Silicon, Python 3.13.2 on host, Python 3.13.18 in the
application container, Docker Engine 29.9.3.

| Check | Actual result |
| --- | --- |
| Frozen dependency synchronization | PASS |
| Ruff lint | PASS |
| Ruff format check | PASS, 15 Python files |
| Pytest with warnings treated as errors | PASS, 12 tests, final run 0.31 seconds |
| Alembic PostgreSQL SQL generation | PASS |
| Compose configuration | PASS |
| Application image builds | PASS for API, migration, worker |
| Initial service startup | PASS, four long-running services healthy |
| Migration revision | `0001_foundation (head)` |
| Repeat migration against existing database | PASS |
| Real worker/database/broker/API smoke | PASS |
| Redis stopped | Readiness 503, Redis false, liveness 200 |
| Redis restored | Full smoke PASS |
| PostgreSQL stopped | Readiness 503, PostgreSQL/schema false, liveness 200 |
| PostgreSQL restored | Full smoke PASS |
| Compose down/up with retained volumes | PASS; migration and full smoke PASS |
| Host-to-API readiness | HTTP 200, all three readiness checks true |
| Application container UID | 10001, non-root |
| Git whitespace check | PASS |

Initial infrastructure failures were resolved: network access was granted, uv cache was
moved into the workspace, Docker Desktop was upgraded/restarted by the user, and scoped
Docker builder-metadata access was granted. A build interrupted by the engine restart was
retried successfully. No acceptance failure remains open for this phase.

The stack is left running on loopback port 8000. Changes are local; no commit or push was
performed. GitHub Actions has not run remotely. There is no claim that payment recovery,
ML, financial evaluation, or tenant isolation exists yet.

## Reproduce the failure checks

After healthy startup, stop only this project's Redis service:

```sh
docker compose stop redis
docker compose exec -T api python -c 'import httpx; c=httpx.Client(base_url="http://localhost:8000",timeout=15); r=c.get("/health/ready"); print(r.status_code,r.json()); assert r.status_code==503; assert c.get("/health/live").status_code==200'
docker compose up --wait --wait-timeout 180
docker compose exec -T api python scripts/smoke.py
```

Repeat using `stop postgres` to exercise the database outage. Always restore services after
these intentional failures. These additional outage checks were run locally; the current
CI workflow includes the core build, migration, worker, and retained-volume restart checks.

## Next milestone

Phase 1 begins with an explicit organization-owned payment/attempt/event domain model,
constraints, state transitions, migrations, idempotent ingestion, audit, and transactional
outbox. A separate plan must precede that implementation. Detection, diagnosis, SHAP, and
statistically meaningful experiments remain required later MVP milestones.
