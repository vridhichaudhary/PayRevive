# Phase 2 — Reproducible synthetic payment ecosystem

Status: COMPLETE for local Phase 2 acceptance, verified 13 November 2026. No recovery or ML implementation phase has started.

## What this phase adds

A versioned offline simulator produces a coherent population of subscription customers,
safe card metadata, monthly payment obligations, first-attempt observations, and diagnosis
feature snapshots. A separate truth directory records hidden failure categories and known
issuer/gateway incidents. The adapter can ingest payments and events through the existing API.

Defaults: seed 42, 6,000 customers, three monthly obligations each, January–March 2026,
INR minor units, demo organization `00000000-0000-0000-0000-000000000001`.
The complete assumptions and limitations are in [the dataset card](dataset_card.md).

## Run it yourself

From the repository root, install the locked environment and generate a new dataset directory:

```sh
uv sync --frozen
uv run python -m simulator.run --config simulator/configs/baseline.json --output work/datasets/demo
```

Open `work/datasets/demo/reports/incidents.png` in Finder or VS Code. Read
`reports/summary.json`, `config.json`, and `manifest.json` alongside it.
The output path must not already exist; choose a new path for another run. For example:

```sh
uv run python -m simulator.run --seed 42 --customers 6000 --output work/datasets/reproduction
```

The CLI configuration file is strict JSON. `--seed` and `--customers` override those file
settings. Unknown keys, incomplete/future observation periods, invalid counts, and non-finite
incident strengths are rejected. `start` must be the first of a month and the full period
must be historical. The default config is committed; generated datasets are ignored by Git
and excluded from the Docker build context.

### Ingest and replay

With Docker Desktop running:

```sh
docker compose up --build --wait --wait-timeout 180
uv run python -m simulator.ingest work/datasets/demo --limit 50
uv run python -m simulator.ingest work/datasets/demo --limit 50
```

The first command ingests up to 50 obligations/events; the repeat should report 50 replays
and zero creations. Default batch size is 100. The adapter is restricted to a local API and
will not follow redirects. A different loopback port can be specified with `--api`.

It verifies payment/attempt file checksums and validates all projected inputs and relationships
before sending the first request. Each payment/event operation remains independently
transactional, so a network failure midway through a batch leaves a partial batch; rerunning
the same artifact safely resumes it through existing idempotency contracts. The adapter never
opens truth, reports, or feature files. A checksum detects changes; it does not authenticate a
malicious dataset author who can rewrite both records and manifest.

**Persistence boundary:** customers, card metadata, and feature snapshots currently remain
versioned offline artifacts. Only obligations and attempt observations are projected into
PostgreSQL by the supported API. This phase does not claim that the online diagnosis API can
load customer features from the database. Durable feature-snapshot ingestion and source
provenance must be implemented before online ML diagnosis. Operational database state remains
PostgreSQL's responsibility; offline generation artifacts are reproducible input, not current
payment state.

## Export structure

```text
dataset/
  config.json
  manifest.json
  operational/
    customers.jsonl
    methods.jsonl
    payments.jsonl
    attempts.jsonl
    features.jsonl
  truth/
    labels.jsonl
    incidents.jsonl
  reports/
    summary.json
    incidents.png
```

JSON Lines stores one typed record per line, allowing later streaming without a spreadsheet
or a giant nested JSON document. JSON uses canonical key order and deterministic record order.
The manifest stores SHA-256 checksums for ten generated files, generator/Python/plot-library
versions, and a synthetic-only marker. It contains no wall-clock run timestamp or output path
that would break reproducibility. The manifest does not hash itself.

Exports are rendered into a temporary sibling directory and renamed only after all required
files exist. Renderer failure does not expose a partial dataset. Existing datasets are never
intentionally overwritten. Consumers should treat the published directory as immutable.

## How relationships work

1. Each customer has a segment, base subscription amount, stable billing day, and tenure.
2. Their card metadata has a linked owner, issuer, network, expiry date, and synthetic reference.
   Network probabilities depend on issuer, rather than drawing every field independently.
3. Each month produces one obligation on that customer's billing day, usually at the same
   amount, with occasional simulated plan upgrades.
4. Failure hazards depend on segment, private liquidity heterogeneity, amount changes,
   month-end timing, expiry metadata, issuer, gateway, and active degradation.
5. Failure is sampled from the combined probability; an overlapping hazard mixture assigns
   the hidden failure category. Reported reasons are independently masked for some failures.
6. Latency has overlapping distributions and non-incident slow tails, preventing a perfect
   latency-based incident rule in this simulation.
7. Features use only information available by the initial outcome observation. The current
   payment updates history only after its feature snapshot has been created.

A missing reported reason does not mean that the simulator lacks a hidden answer. That is
useful for later diagnosis of ambiguous failures. A reported reason is available for deterministic
mapping but is excluded from the classifier input allowlist when predicting the hidden category.
Identifiers are join metadata; `Features.model_inputs()` explicitly excludes them.

This is a stylized probability model. The expiry field is not a processor eligibility check:
expired-looking metadata raises probability but does not force every outcome to fail. Do not
use these synthetic associations to justify retrying an explicitly terminal decline.

## Reproducibility and paired comparisons

Each customer/payment gets a semantic random stream derived from seed, generator version,
and entity key using SHA-256. Changing an output filename does not consume random numbers.
The dataset identity also includes configuration so changed scenarios cannot silently reuse
source event identities against the ingestion API.

The no-incident comparison disables the injected effects while holding customer cohorts,
amounts, timing, issuer/network/gateway assignments, and outcome random draws comparable.
This checks that the deliberate perturbations have their intended effect. It is not a
randomized real-world experiment or a recovery-policy comparison.

Byte-identical artifacts are expected for identical configuration and the same software
and rendering environment. Cross-platform font/rendering differences or Python/library
upgrades may change bytes and require re-verification; the manifest records versions rather
than promising universal portability. Source changes that alter generated data require a
generator-version bump.

The chart displays daily failure rates and shades each panel's primary incident window.
Days without attempts are gaps, not invented zero-failure days. Unshaded differences can
come from another incident affecting a subset of that panel's issuer/gateway cohort.
Small datasets flag low incident sample counts; the plotted spikes alone are not a detector
evaluation. A detector still needs independent temporal validation, delay, precision/recall,
and false-alert measurement in its later phase.

## Files and concepts

| Module | Responsibility and concept |
| --- | --- |
| config.py | Validated, immutable scenario configuration; rejects impossible input periods |
| models.py | Explicit entity contracts and a classifier feature allowlist |
| generator.py | Coupled seeded population, competing failure hazards, time-ordered history |
| artifacts.py | Canonical serialization, checksums, complete-directory publication |
| report.py | Paired-cohort diagnostics and a headless Matplotlib chart |
| run.py | Generation CLI with committed JSON defaults |
| ingest.py | Narrow operational projection into existing HTTP contracts |
| tests/ | Reproducibility, temporal history, relationship, leakage boundary, corruption/replay tests |
| scripts/simulator_smoke.py | Real generated-data ingestion and replay in Docker/CI |

Matplotlib was added for a standalone, inspectable chart. The generator itself uses Python's
standard library and existing Pydantic rather than adding an ML framework before evaluation
exists. The Docker image uses a writable temporary Matplotlib cache and no GUI backend.

## Verify

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -q -W error
docker compose --profile test run --build --rm test-runner
docker compose exec -T api python scripts/smoke.py
docker compose exec -T api python scripts/simulator_smoke.py
```

Tests use three seeds (7, 42, 99) for paired incident sanity checks. They verify exposed cohort
size and an increased failure rate, but do not present the threshold as an industry benchmark.
They also exercise an injected rendering failure, corrupt files, invalid operational relations,
existing output protection, truth-free ingestion, and idempotent replay. PostgreSQL tests
remain separate and are actually executed, not replaced by mocked HTTP transport tests.

## Acceptance results

| Acceptance check | Actual result |
| --- | --- |
| Unit tests with warnings as errors | 47 passed, final run 5.86 seconds |
| Real PostgreSQL integration tests | 19 passed, 0.74 seconds |
| Paired incident sanity checks | Passed across seeds 7, 42, 99 |
| Full default export | 6,000 customers, 6,000 methods, 18,000 payments/initial attempts |
| Full independent reproduction | All ten artifacts plus manifest byte-identical |
| Operational/truth boundary | Adapter works after truth and report directories are removed |
| Temporal feature history and model allowlist | Passed |
| Masked reason scenario | Disclosure changes do not change hidden outcomes |
| Existing path and injected renderer failure | No overwrite or published partial dataset |
| Corrupt checksum and altered relationships | Rejected before HTTP writes |
| Real API ingestion | 50 payments and 50 events created |
| Real API replay | 50 payments and 50 events replayed; zero creations |
| Rebuilt container smoke | Infrastructure, lifecycle, generated population, replay passed |
| Ruff lint and formatting | Passed; 40 Python files |
| Frozen dependency sync, Compose config, whitespace | Passed |

The default seed produced 3,361 failures (18.67%), including 1,151 failures with a missing
reported reason. These are chosen synthetic distributions, not real payment benchmarks.

| Injected cohort | Attempts | Incident failure rate | Same cohort with incidents disabled |
| --- | ---: | ---: | ---: |
| issuer_a, 11–13 January | 231 | 50.6% | 16.9% |
| gateway_y, 21–22 January | 170 | 51.8% | 15.9% |
| issuer_c, 10–11 February | 113 | 50.4% | 12.4% |

The chart was visually inspected. The disposable test database was removed; four demo
services remain healthy. No unresolved local acceptance failure remains. Regular publication
began with core-generator commit `d7cb3d2`; its hosted CI passed, as did the preceding
foundation/ingestion commit. The final artifact/adapter commit runs the updated CI workflow;
its eventual hosted status is reported with the delivery rather than assumed here.

## What remains

No retry-policy outcomes, recovered revenue, confidence intervals on financial lift, trained
classifiers, SHAP values, or anomaly detector are produced here. Subsequent phases must run
safe policies through an actual execution simulator and compare monetary outcomes on matched
cohorts with seeds and uncertainty intervals. A favorable synthetic result cannot establish
real-world effectiveness. Full multi-tenancy and external provider ingestion remain deferred.

The next planned phase is rule-based recovery and independent eligibility/execution safety.
Degradation detection, diagnosis, temporal ML evaluation, and SHAP remain core MVP requirements.

## Interview questions

- Why use synthetic data? It provides controlled labels and incidents for a problem without
  an identified suitable public dataset; it cannot validate real-world generalization.
- Why keyed random streams? They isolate randomness by entity and enable reproducible,
  comparable scenarios without dependence on incidental loop order or output paths.
- Why keep truth separate? Hidden labels make evaluation possible but would create leakage
  if runtime decisions or model inputs could access them.
- Why compare the same cohort? Different customers or payment amounts could explain a
  difference that appears to be caused by the incident or policy.
- Does a high synthetic classification score prove useful intelligence? No; it can simply
  reflect generator rules. Temporal and changed-scenario validation are still necessary.
