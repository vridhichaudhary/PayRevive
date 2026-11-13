# PayRevive development contract

Read README.md and docs/architecture.md before changes. Work phase by phase; do not mark a
phase complete or proceed past unresolved acceptance failures. Explain major designs first.
Use a modular monolith. PostgreSQL is the business source of truth; Redis transports work.
Initial domain: subscription cards, INR, one explicit demo organization; preserve future
organization ownership. Distinguish payments, attempts, and events. Never store PAN/CVV or
execute real financial transactions. Simulator first; sandbox choice deferred.
Detection, failure diagnosis, temporal ML evaluation, and SHAP are core MVP components.
Experiments need comparable cohorts, reproducible seeds, monetary outcomes, uncertainty,
and honest synthetic-data limitations. Business safety policies are not verified legal rules.
Keep recommendation, eligibility, execution, and training/inference separate. Future execution
must handle duplicate/out-of-order events, worker crashes, competing actions, cancellation,
unknown outcomes, idempotency, and reconciliation. Compliance fails closed.
Run meaningful checks and report actual results and limitations. Maintain setup, architecture,
data/model cards and experiment reports as those components are added. Explain concepts in
plain language suitable for a CS/AI undergraduate and interview preparation.

## Git workflow

The user requests regular commits and pushes. After each tested, meaningful milestone, make
an accurately scoped commit and push it to the configured GitHub remote. Do not wait for an
entire phase if a smaller coherent component is complete. Never commit secrets, environments,
cache directories, or generated local data. Report commit IDs and any push/CI failures.

## Approved ML/notebook workflow

Reusable data, features, detection, training, evaluation and SHAP logic belongs in tested Python
modules. Focused notebooks import those modules to explain real generated-data experiments.
No duplicated business/ML implementation, hidden cell state, hand-edited metrics, or runtime
Jupyter dependency. Run notebooks from a fresh kernel with manifests and pinned dependencies;
publish executed reports as artifacts. The serving image must not install notebook tooling.
The first milestone is Phase 4A: shared feature provenance and the data/feature audit notebook.
Do not mark the whole Phase 4 complete before detection, diagnosis, temporal evaluation and SHAP.
