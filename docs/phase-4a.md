# Phase 4A — Feature provenance and the first executable analysis

Status: complete. This milestone does not complete Phase 4.
Degradation detection, diagnosis, temporal model evaluation, model comparison, and SHAP
remain mandatory subsequent components. No financial evaluation or model performance is claimed.

## Why this milestone comes first

A classifier needs trustworthy inputs available at its prediction time. A notebook needs the
same contract and genuine data, rather than its own CSV transformations. This milestone
establishes those boundaries before model fitting or alert-threshold selection.

The implementation is hybrid: shared modules implement loading, validation, audit statistics,
and plotting; one focused notebook asks questions, calls those modules, and explains results.
A CLI performs the same audit without a notebook. Production never starts a notebook kernel.

## Shared feature contract

`backend/app/features/contracts.py` is the single definition of twelve candidate inputs and
four excluded join/time fields. `simulator.models.Features` re-exports that exact class;
there is no duplicate feature model. Generator data identity and operational serialization
remain compatible with the existing version-1 corpus. Candidate rationales live beside the
contract and feed the notebook's table.

Values use bounded types, finite rates, explicit UTC calendar alignment, and consistent
history semantics. Amounts remain integer paise. The first observation has no prior history,
so its success rate is null rather than an invented zero. After history exists, its rate must
be present and bounded. UTC-equivalent timestamps normalize consistently.

`model_inputs()` selects only the allowlist. Payment/event/organization IDs and exact timestamps
remain join metadata. Reported reason, hidden failure label, incident membership, outcomes,
and source manifest hashes are not classifier inputs. This is a schema/leakage boundary, not
proof that every candidate feature is useful or that every possible leakage path is absent.

Latency is known after the response, making it eligible for failure diagnosis but not a
pre-attempt prediction. Customer tenure is fixed baseline metadata in generator v1. Expiry
metadata is probabilistic in this stylized generator, not processor eligibility evidence.
Feature selection and ablations must wait for actual training/validation experiments.

## Verified data loading

`ml/data/loading.py` checks the producer/configuration dataset identity, fixed manifest paths,
checksums for configuration/operational files, counts, organization ownership, monthly customer
cohorts, method/payment relationships, amounts, gateways, UTC observations, and history based
only on earlier observed initial payments. Joins use record identity, not incidental file order.
Symlink/path escapes are rejected. Data is currently materialized in memory for local demo scale.

Operational loading does not open truth or rendered reports, and works when those directories
are absent. `ml/data/evaluation.py` explicitly loads and verifies evaluation labels/incidents.
That separate module is not imported by the serving feature service. Audit code uses truth
only for descriptive analysis after the operational data is validated.

Checksums catch changes and bind provenance; an author able to rewrite records and checksums
is not authenticated by those hashes. Structural checks reject several semantic inconsistencies,
but this is not cryptographic attestation that a real payment observation was truthful.
The data remains entirely synthetic.

## Immutable online feature snapshots

Migration `0004_features` adds `observation_features`, bound by a composite foreign key to the
organization, payment, and event. Each snapshot records feature schema, generator version,
dataset ID, source manifest SHA-256, values, and its own content fingerprint.

POST/GET:

```text
/v1/organizations/{org}/payments/{payment}/events/{event}/features
```

POST returns 201 for creation, 200 for identical replay, 409 for conflicting values/provenance,
and 404 for mismatched resource ownership. Unknown fields, including hidden labels, return 422.
The server verifies actual reference, amount, source event ID/time, gateway, and organization.
Feature v1 supports terminal initial observations only. It does not fabricate snapshots for
recovery attempts by copying stale initial metadata.

The snapshot and its audit entry commit together. PostgreSQL protects snapshots from ordinary
updates/deletes/truncation. Payment detail includes the snapshots. The API records declared
as-of provenance; it cannot reconstruct the complete external customer history from a partial
online import. Complete-bundle verification is performed by the local adapter before upload.

A changed source archive manifest is intentionally a provenance conflict, even if selected
values look unchanged. Replay the original verified artifact; do not silently overwrite its
lineage. Re-rendering under different software may change a manifest. Generator changes that
alter data semantics need a producer-version change and appropriate new identities.

Feature writes do not grant consent, change recovery permissions, or trigger ML-driven
execution. Existing payment/event-only ingestion remains compatible. Use the opt-in adapter:

```sh
uv run python -m simulator.ingest work/datasets/demo --limit 50 --with-features
```

Both successful and failed initial observations can carry snapshots. A failure-only import
would not provide the proper denominator for degradation detection.

## Executable notebook and reproducible reporting

The first notebook is `notebooks/01_data_and_feature_audit.ipynb`. Its cells contain imports,
parameters, shared module calls, and presentation. No reusable function/class implementation,
model fitting, or duplicated metric logic lives there. The source is committed without outputs.

```sh
uv sync --frozen --group analysis
uv run --group analysis python -m simulator.run --output work/datasets/demo
uv run --group analysis python -m ml.notebooks.run --dataset work/datasets/demo --output work/analysis/data-audit
```

Output:

```text
data-audit/
  executed.ipynb
  report.html
  run_manifest.json
  audit/
    audit.json
    overview.png
    temporal.png
```

The runner uses a fresh kernel with an explicit current-interpreter kernelspec. Kernel/client
sockets are closed on success and failure. Temporary caches/runtime/configuration and private
IPC sockets on macOS/Linux stay out of published artifacts. It clears stale cell outputs,
stops on cell errors, leaves the original source untouched, and publishes only a complete run.
The renderer date-type error and client-socket cleanup failure found during development were
fixed and the same strict checks rerun; warnings were not suppressed to obtain a pass.

The run manifest binds source notebook/runner hashes, source dataset manifest, selected cutoff,
kernel Python, and output checksums. The audit records source-module hashes, Git revision and
dirty state, Python, and dependency versions. A dirty tree is reported honestly rather than
claiming that an uncommitted experiment exactly matches its base commit. Final delivery is
regenerated against a clean published commit.

The serving image does not install pandas, JupyterLab, ipykernel, nbclient, or nbconvert.
Those tools are in the optional `analysis` dependency group. Analysis tests and headless
execution run in a separate CI job; its actual executed HTML/notebook is uploaded as an artifact.

## What the notebook explains

1. Which real generated files were loaded and validated.
2. What belongs to the exploratory slice and which observations are withheld.
3. Initial success/failure counts, hidden category imbalance, and missing reported reasons.
4. Subscription amount/segment relationships and expected empty-history missingness.
5. Every candidate feature's meaning, availability, and exclusion risks.
6. Visible injected incidents, missing-traffic gaps, and why these are not detector metrics.
7. Which conclusions are limited to the synthetic generator and which evaluations remain.

The default audit excludes the final generated month from displayed outcome statistics.
It does not promise an untouched final test: seed 42 has already been explored in earlier
phases. Before training, reserve suitable new evaluation data, freeze calendar cutoffs,
fit preprocessing/selection on training data only, tune on validation, and evaluate final
models without using test outcomes to select them. Existing-customer temporal performance
and unseen-customer performance are distinct questions. Changed scenarios are also needed;
a new random seed alone does not establish a new generating process.

## Verification

```sh
uv run --group analysis pytest -q -W error
uv run --group analysis pytest -m analysis -q -W error
docker compose --profile test run --build --rm test-runner
docker compose exec -T api python scripts/features_smoke.py
```

The default unit selection excludes PostgreSQL/analysis markers. Each separate suite must
actually run; deselection is not acceptance. Tests cover hidden-field rejection, corrupt
files/relationships/history, key-based joins, timestamp mismatch, cross-org resources,
concurrency, audit rollback, immutable history, fresh execution, source preservation, genuine
CLI/notebook agreement, and no partial publication after a deliberately failing cell.

## Actual findings and acceptance

Phase 4A is complete. Whole Phase 4 remains incomplete: degradation detection, diagnosis,
model comparison, temporal evaluation and SHAP are still required.

Verified locally on 2026-11-13:

- 98 unit tests passed, with warnings treated as errors.
- 70 PostgreSQL tests passed against the real database.
- 2 fresh-kernel analysis tests passed, with warnings treated as errors.
- Ruff lint and formatting checks passed; Git whitespace checks passed.
- Live API feature ingestion, event binding, immutable provenance and replay passed.
- The serving image excludes nbclient, nbconvert, ipykernel, jupyterlab and pandas.

The full generated corpus has 6,000 customers and 18,000 initial observations. The notebook
explores 12,000 observations before March 2026: 9,846 successes and 2,154 failures (17.95%).
6,000 first observations have no prior success-rate history; this is deliberately null.
733 explored failures have no reported reason. See the [actual findings](experiment_reports/data-audit-v1.md)
for provenance and limitations. These are descriptive synthetic findings, not ML performance.

## Interview explanations

- **Why keep notebooks?** They put the question, real evidence, and interpretation together.
- **Why shared modules?** The CLI, notebook and future inference use the same contracts and
  transformations, avoiding a research/serving discrepancy.
- **Why not use IDs as features?** They identify examples, and can encourage memorization or
  scenario leakage rather than learning transferable relationships.
- **Why null history?** No prior observation is not the same as an observed zero success rate.
- **Why bind feature time?** A value known after the failure must not masquerade as information
  available before an attempt; future data must not enter historical predictions.
- **Why separate truth?** Evaluation needs labels, while runtime inputs must not contain them.
- **What does the chart prove?** The injected synthetic mechanisms are visible, not that a
  detector works or a feature/model generalizes to real payments.

Next milestone: implement degradation detection and its explanatory notebook. Diagnosis,
model comparison, temporal evaluation, SHAP, and subsequent monetary experiments remain planned.
