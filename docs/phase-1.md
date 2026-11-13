# Phase 1 — Domain model and transactional ingestion

Status: COMPLETE for local Phase 1 acceptance, verified 13 November 2026. No later phase has started.

## Objective and boundaries

Persist an organization-owned subscription obligation, its collection attempts, and received
observations. This phase provides trustworthy history for future detection, diagnosis, and
recovery. Only simulator ingestion exists. No processor requests, recovery execution, customer
messaging, outbox dispatch, ML models, or financial-performance claims are implemented.

## The domain in concrete terms

A subscription invoice for INR 1,499 is one payment with `amount_minor=149900`. Attempt 1
can fail; attempt 2 can succeed. A pending observation and a failure observation for attempt
1 are two events about one attempt. Re-delivering an event is not another attempt.
Amounts are positive signed-64-bit integers in paise, never floating-point currency values.

The seven tables are:

| Table | Responsibility |
| --- | --- |
| organizations | Explicit ownership boundary; one fixed demonstration organization is seeded |
| payments | Obligation, immutable reference/amount/currency, current projected status |
| payment_attempts | One observed execution numbered within a payment; current projected status |
| payment_events | Immutable received observations with source identity, event time, receipt time |
| audit_events | Append-only application history with actor and structured details |
| outbox_messages | Durable pending work created atomically with each distinct event |
| application_metadata | Global Phase 0 simulation marker, not tenant business data |

Organization IDs participate in composite foreign keys. An attempt owned by organization B
cannot reference organization A's payment even when both organization IDs individually exist.
Each business query also scopes by organization. This is groundwork for tenancy, not an
authentication system: callers can choose an organization path, and no authorization is claimed.
The API remains loopback-only. Database administrator privileges can disable triggers; the
current shared local-demo database role is not a tamper-proof audit security boundary.

Customer and payment-method entities will be introduced with the synthetic ecosystem when
their required attributes are concrete. There are no raw card fields or arbitrary event blobs.
Unknown fields are rejected. Callers must still use safe synthetic references: never put
sensitive data inside an otherwise valid identifier.

## Transaction boundaries and concurrency

Creation uses a database unique constraint on `(org_id, reference)` and INSERT ON CONFLICT.
Replay returns the same payment, but changing its original amount or currency returns 409.
The creation audit record is in the same transaction as the payment.

Event identity is `(org_id, provider, account, source_event_id)`. PostgreSQL takes a
transaction-scoped advisory lock derived from this identity, then locks the payment row.
This serializes conflicting identity reuse even across different payments, while the payment
lock serializes different events updating the same payment. All code takes locks in that order.
The database unique constraint remains the final deduplication guard. A hash collision can
serialize unrelated identities but cannot make them equal.

A canonical SHA-256 fingerprint includes payment identity and validated observation fields.
Defaults are filled and timestamps normalized to UTC before hashing. A semantic replay returns
200 with the same event ID; changed content returns 409 without changing records.

One transaction performs all of these operations:

1. Validate scoped identity and lock ownership.
2. Create or update the observed attempt.
3. Append the event.
4. Project the payment's current state.
5. Append the audit record.
6. Insert an outbox message carrying organization, payment, and event IDs.

Failure at step 6 rolls back steps 2–5 as well. A process dying before commit has no committed
effect; a caller losing the response after commit can replay the identity. A generic database
error returns 503 without SQL or credential details. Locks have bounded statement waits;
clients should retry 503 responses with the same identity rather than inventing a new event.

The outbox has no publisher yet. `published_at` remains NULL. A future dispatcher may publish
twice after a crash; consumers must be idempotent. This phase does not claim exactly-once queue
processing or implement a dispatcher without a meaningful downstream consumer.

## State projection and ordering

- Success is absorbing for an attempt and for its payment. Late or newer failures remain
  recorded but cannot erase an observed success. Refunds/reversals are outside this MVP domain.
- Without success, attempt state uses event time. Equal timestamps use deterministic precedence:
  failed over pending. Older observations cannot overwrite newer attempt state.
- Without a payment success, the highest observed attempt number determines payment state.
- Missing prior attempts are allowed: observing attempt 2 first does not invent attempt 1.
- Ingesting an attempt is recording history, not permission to execute a retry.
- Future observation timestamps are rejected. Historical simulation timelines are supported;
  a future virtual-clock runner will need an explicit clock contract rather than bypass validation.

There are two timestamps because observation order and delivery order differ. Receipt time
helps investigate delays; event time drives the current projection. Provider adapters will
need their own verified ordering semantics when introduced.

## HTTP contract

The seeded demo organization is `00000000-0000-0000-0000-000000000001`.

| Endpoint | Behavior |
| --- | --- |
| POST /v1/organizations/{org_id}/payments | 201 create; 200 identical replay; 409 conflicting reference |
| POST /v1/organizations/{org_id}/payments/{id}/events | 201 new event; 200 replay; 409 identity conflict |
| GET /v1/organizations/{org_id}/payments/{id} | Consistent snapshot of obligation, attempts, events, audit |

Unknown/scoped-out resources return 404. Invalid inputs return 422. Storage failures return 503.
Payment reference and source event identity are the idempotency keys for these simulator APIs;
no separate Idempotency-Key header or invented provider webhook contract is required here.
Failure reasons are internal configurable simulation categories, not external decline codes.

The detail endpoint uses a repeatable-read transaction so payment state and its timeline are
read from one consistent snapshot. A timeline is currently returned in full; pagination and
retention should be added before exposing high-volume external ingestion.

## Run and verify

```sh
docker compose up --build --wait --wait-timeout 180
docker compose exec -T api python scripts/smoke.py
uv run pytest -q -W error
docker compose --profile test run --build --rm test-runner
```

The unit run explicitly selects non-PostgreSQL tests. The Compose test profile explicitly
selects PostgreSQL tests and runs migrations first. Its PostgreSQL instance uses tmpfs and a
separate `payrevive_test` database; tests refuse to use the demonstration database. Unit-test
success alone does not establish this phase's acceptance. CI runs both suites and the smoke
check. No GitHub-hosted workflow execution is implied by local results.

The extended smoke test creates an INR payment, ingests/replays a failure, rejects conflicting
reuse, ingests success and a late failure, then verifies durable event/audit/outbox counts.
Each run uses unique references and leaves a useful demo lifecycle in the local database.

## Try it in the API documentation

Open http://127.0.0.1:8000/docs. Expand the create-payment endpoint, click Try it out,
and use the demo organization UUID with this body:

```json
{"reference": "subscription-demo-001", "amount_minor": 149900, "currency": "INR"}
```

Copy the returned payment ID into the event endpoint and send:

```json
{
  "source_event_id": "demo-failure-001",
  "attempt_number": 1,
  "status": "failed",
  "reason": "insufficient_funds",
  "occurred_at": "2026-11-01T10:00:00Z"
}
```

Send the same body twice: the first response is 201 and the second 200. Change only the
reason to `unknown`: the response should be 409. Then send a new event ID for attempt 2
with status `succeeded`, reason null, and a later historical timestamp. GET the payment to
see both attempts and the full timeline. These are synthetic observations; no retry executes.

## Files changed

- `backend/app/domain/tables.py`: explicit SQLAlchemy Core metadata and ownership constraints.
- `backend/app/domain/state.py`: pure state-transition function, independent of HTTP and storage.
- `backend/app/ingestion/schemas.py`: narrow validated simulator inputs, integer money, UTC time.
- `backend/app/ingestion/service.py`: transaction orchestration and scoped persistence.
- `backend/app/ingestion/routes.py`: HTTP adapters; domain logic stays in the service.
- `backend/alembic/versions/0002_domain.py`: frozen DDL, demo seed, immutable-history triggers.
- `main.py`, `db.py`, `alembic/env.py`: route wiring, safe error translation, expected revision,
  schema metadata. Migrations never import mutable domain tables to reconstruct old DDL.
- `backend/tests/test_domain.py`, `test_ingestion_postgres.py`, `test_foundation.py`: behavior,
  concurrency, rollback, tenant boundary, schema parity, and error-handling tests.
- `scripts/domain_smoke.py`, `scripts/smoke.py`: actual HTTP-to-database verification.
- `Dockerfile`, `compose.yaml`, `pyproject.toml`, CI: separate test image and isolated database.

SQLAlchemy Core was selected for explicit SQL and transaction boundaries without an ORM
relationship graph. SQLAlchemy remains the database abstraction; no extra framework was added.
The frozen migration includes the generated PostgreSQL DDL so future metadata edits cannot
change the meaning of an already-applied migration.

## Interview questions

**Why does an application-level duplicate check alone fail?** Two requests can both see no
row before either inserts. Locks coordinate application behavior; database uniqueness protects
the invariant even if a caller bypasses that behavior.

**Why lock a payment as well as an event?** Distinct events can update the same current state.
Without coordination, they can overwrite each other's projection even though neither is a duplicate.

**Why an outbox?** Saving an event and publishing a task are separate systems. A crash between
them can lose work. Saving pending work with the event makes a later retryable dispatcher possible.

**Why test PostgreSQL instead of SQLite?** Composite constraints, advisory/row locks, triggers,
JSONB, and transaction isolation are central behavior, not interchangeable implementation details.

**Why distinguish an observation from financial truth?** The simulator reports status here.
A real integration must authenticate providers and reconcile conflicting outcomes; this schema
alone does not establish a real-world settlement or revenue claim.

## Acceptance results

| Acceptance check | Actual result |
| --- | --- |
| Unit tests, warnings as errors | 24 passed, final run 0.40 seconds |
| PostgreSQL integration tests, warnings as errors | 19 passed, final run 0.69 seconds |
| Six concurrent identical events | One event, attempt, event audit, and outbox effect |
| Concurrent distinct events for one payment | Success preserved; two durable messages |
| Concurrent identity reuse across two payments | One accepted, one 409; no second attempt artifact |
| Conflicting replay | 409, unchanged row counts |
| Equivalent UTC/offset timestamp replay | Recognized as duplicate |
| Out-of-order events | No success regression or fabricated missing attempts |
| Injected outbox failure | Event, attempt, state, audit, outbox all rolled back |
| Organization boundary | Scoped 404 and database composite-FK rejection |
| Immutable event/audit history | Database rejects UPDATE, DELETE, TRUNCATE |
| Payment creation replay | Same identity; conflicting amount rejected |
| Actual HTTP contract | Create, ingest, replay, conflict, invalid input, detail verified |
| Database unavailable response | Generic 503 without private details |
| Migrated schema versus current metadata | No differences |
| Fresh test database migration | Successful 0001 then 0002 application |
| Existing demo database upgrade | Successful 0001 to 0002 transition |
| Repeated migration | Successful; current revision 0002_domain |
| Real Redis/Celery/PostgreSQL probe | Passed |
| Real HTTP lifecycle plus durable counts | Passed before and after stack restart |
| Pre-restart demonstration record | Successful status and all three events retained |
| Ruff lint and formatting | Passed; 27 Python files formatted |
| Frozen dependency sync, Compose configuration, whitespace | Passed |

The two suites use explicit marker selection: each run deselects the other suite rather than
silently skipping its acceptance. Both suites were actually executed. The test database was
removed after verification; the four demonstration services remain healthy on loopback 8000.
No unresolved local acceptance failures remain. CI was updated but has not run on GitHub.
All changes remain local; no commit or push was performed.

No new third-party runtime dependency was needed. The only environment permission requested
was access needed to use Docker's local socket/build metadata and network during verification.

## Next phase

The synthetic ecosystem: customers/payment methods with safe metadata, correlated payment
behavior, deliberately injected incidents, reproducible seeds, and hidden evaluation truth.
Detection, diagnosis, temporal ML evaluation, SHAP, and monetary experiments remain required.
