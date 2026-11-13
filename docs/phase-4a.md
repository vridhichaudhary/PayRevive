# Phase 4A — Feature provenance and data-audit notebook

Status: feature contracts/provenance implemented; notebook execution is in progress.
This milestone does not complete Phase 4. Degradation detection, diagnosis, temporal model
evaluation, comparisons, and SHAP remain mandatory subsequent components.

One schema in `app/features/contracts.py` now owns the candidate feature values, validation,
allowlist, and feature rationale. The simulator re-exports that exact class; there is no second
feature implementation. The operational dataset loader verifies configuration identity,
checksums, entity relationships, UTC observation alignment, cohort membership, and history
computed only from earlier observations. Evaluation truth has a separate explicit loader.

`observation_features` stores an immutable snapshot bound to organization, payment, and event.
The envelope records feature schema, generator version, source dataset and manifest SHA-256.
A hash provides traceability and change detection, not proof that an untrusted caller's claims
are truthful. The verified local adapter checks the source files and complete relationships.
The server checks the actual payment reference, amount, event identity/time, gateway, and
organization. Feature v1 supports terminal initial-attempt observations only; reusing stale
initial features for recovery attempts is rejected.

POST/GET `/v1/organizations/{org}/payments/{payment}/events/{event}/features` expose the snapshots.
POST returns 201 on creation, 200 on identical replay, and 409 on conflicting values/provenance.
Unknown/hidden fields are rejected. Database triggers prevent ordinary history edits/deletion.
A snapshot and its audit entry commit together. Snapshot writes do not grant consent, modify
recovery permissions, or trigger an ML-driven action. Payment detail includes feature snapshots.

`simulator.ingest --with-features` opts into full bundle verification and snapshot attachment.
The default payment/event-only adapter remains compatible and never needs evaluation truth.
All initial outcomes, including successful ones, can carry features: detection needs a valid
success/failure denominator, not a failure-only dataset.

The seed-42 corpus is exploratory; prior EDA means it must not later be advertised as an
untouched final model test set. The notebook will distinguish exploratory and withheld rows,
and later training must freeze its evaluation protocol and use appropriately reserved data.

Acceptance requires clean-kernel notebook execution using actual generated data, shared
analysis functions/CLI, immutable cross-organization snapshot checks, genuine report artifacts,
and absence of Jupyter dependencies in the serving image. Results will be recorded after
all those checks pass.
