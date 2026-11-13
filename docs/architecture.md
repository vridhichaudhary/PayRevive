# Architecture and decision record

## Approved boundaries

Use case: subscription card payments. Initial currency: INR. Delivery: local demo first.
One demonstration organization initially; future business entities, queries, task payloads,
and authorization must preserve explicit organization ownership. A single default organization
is not permission to leave tenant ownership implicit. Phase 1 introduces the actual schema.

The backend is a modular monolith: one Python codebase, independently running API and worker.
PostgreSQL owns business state, schedules, audit history, and outbox records. Redis transports
work. A Redis task result is short-lived diagnostic data, never a financial record.
Training is separate from inference; neither ML nor a queue may bypass eligibility checks.
Degradation detection, diagnosis, temporal evaluation, and SHAP remain core MVP requirements.

## Phase 0 modules

- `config.py`: typed configuration from environment variables. Invalid execution modes fail
  during startup. URLs use secret wrappers to reduce accidental representation/log exposure.
  Secret wrappers do not encrypt credentials and validation errors must not be logged raw.
- `db.py`: SQLAlchemy engine factory; bounded connection, pool, and statement waits.
- `health.py`: independent PostgreSQL/schema and Redis probes, with generic failure logs.
- `main.py`: application factory and resource lifespan; connection pools close on shutdown.
  Readiness is a synchronous route, so blocking drivers run in FastAPI's thread pool.
- `worker.py`: JSON-only Celery messages, UTC, bounded tasks, low prefetch, ephemeral results.
- `tasks.py`: a harmless probe that checks the simulation marker in PostgreSQL.
- Alembic: versioned database changes. A metadata marker proves migration ran without
  prematurely committing to the payment schema. It is not a tenant-owned business table.

## Why two health endpoints?

Liveness asks: “Can the API process answer?” Readiness asks: “Can it use its dependencies
and expected schema?” A temporary database outage should remove readiness, not imply that
restarting an otherwise healthy API process will repair the database. Readiness is a
point-in-time observation, not permission for a future financial action. Worker health is
checked separately; the smoke test verifies end-to-end task execution.

## Why migrations instead of create_all?

A migration is a reviewed, ordered transition between schema versions. Running ORM table
creation on each startup does not safely describe upgrades to existing data. The one-shot
Compose migration service must succeed before the API/worker starts. Application startup
never changes schema implicitly. Future migration changes must update the expected revision.

## Why PostgreSQL rather than Redis for schedules?

A payment scheduled two days ahead must survive broker problems, cancellation, approval
changes, and restarts. Future phases persist due actions and dispatch short-lived tasks.
The broker may redeliver; workers must use persistent state and stable operation keys.
The current worker probe introduces no payment execution or durable scheduling yet.

## Future domain and execution contract

A payment is the obligation; an attempt is one execution; an event is an observation.
Each has explicit identity. Provider event deduplication is scoped by organization,
provider/account, and event ID; it differs from action execution idempotency.
Detection runs independently of per-payment diagnosis and recovery.

Future database transactions atomically record business changes, audit entries, and outbox
work. Dispatchers may publish twice; consumers must be idempotent. A payment-level guard
must coordinate competing actions, not merely two workers processing the same action.
Processor timeouts become unknown outcomes. Reconcile before considering another execution.
Eligibility rechecks current preferences, status, budgets, terminal reasons, approvals, and
policy version immediately before execution. Missing eligibility validation fails closed.

## ML and evaluation contract

Explicit decline reasons use deterministic mapping; ML addresses ambiguity without label
leakage. Features must be available at prediction time; temporal partitions and additional
held-out generator configurations test robustness. SHAP explains model contributions,
not causal proof. Low confidence can abstain rather than authorize a retry.
Experiments use comparable cohorts, a shared horizon, reproducible randomness, direct
monetary outcomes, costs, and uncertainty intervals. Hidden simulator truth remains separate.

## Phase 0 historical limitations and tradeoffs

No auth, RBAC, public deployment, tenant guard, outbox, audit store, payment state machine,
or ML exists in Phase 0. Only loopback API exposure is intended. Application and migration
roles share local-demo credentials; least-privilege roles precede external deployment.
Runtime images use explicit version tags; tags are not immutable digests. Dependency upgrades
and image refreshes require verification. No provider client or live financial path exists.

## Interview questions

- Why is readiness different from liveness? Dependency failures and process failures need
  different operational responses.
- Does a queue guarantee exactly-once execution? No; correctness must survive redelivery.
- Does a database lock prevent every duplicate charge? No; an external success followed by
  a worker crash still requires idempotency and reconciliation.
- Why not start with microservices? Module boundaries provide separation without extra
  network failures, deployments, or distributed transaction overhead.

## Primary references

- https://docs.docker.com/compose/how-tos/startup-order/
- https://fastapi.tiangolo.com/advanced/events/
- https://docs.celeryq.dev/en/stable/userguide/configuration.html


## Phase 1 implementation

See [the Phase 1 guide](phase-1.md) for the current schema, locking, state projection,
organization scoping, immutable history, atomic outbox, and verification results. The Phase 0
limitations above describe that earlier milestone. Phase 1 adds those domain components but
does not add authorization, public deployment, recovery execution, or outbox dispatch.


## Phase 2 implementation

The [synthetic ecosystem](phase-2.md) is a versioned offline input/evaluation module with
separate operational and hidden-truth artifacts. Its API adapter projects only supported
payment/event inputs; customer/card profiles and feature snapshots remain offline until a
reviewed ingestion contract is added. No queue consumer reads evaluation truth. PostgreSQL
remains the operational business source of truth. Policies, financial experiments, detector,
diagnosis, and SHAP have not been implemented by this generator milestone.
