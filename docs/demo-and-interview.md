# PayRevive demo and interview guide

PayRevive is a local simulator for failed INR subscription payments. It connects a
payment event and its audit history to a consent-gated recovery action, and measures
how retry policies compare on matched synthetic cohorts.

## Five-minute demo

Start with the [cold-start guide](phase-6.md). Open the dashboard at the port
configured in Compose (8000 by default).

1. Filter to failed payments. Explain that this amount includes payments that may be
   ineligible for retries. Select a generated reference and inspect its event and feature snapshot.
2. Open `dashboard-demo-approval` while the seeded action is pending. Show the reported
   issuer failure, recommendation, and approval requirement. Approve the simulator retry,
   then refresh and inspect the resulting attempt, outcome and audit entries. The retry may
   succeed or fail. Use a fresh demo stack if the seeded action has already been handled.
3. Open Replay analysis. Point out the different datasets used for detection and diagnosis.
   The optional diagnosis report loads via the command in the cold-start guide. Explain
   one SHAP attribution using its background probability and signed contribution.
4. Compare fixed and reason-based retry in the experiment panel. Explain why settlement
   in the control arm is subtracted before attributing value, and why failed retries still cost money.

## How to explain the architecture

- **Payment, attempt, event.** The payment is the obligation. An attempt represents one
  execution. Events record what was observed about that execution. The system can retain
  duplicate-delivery and ordering safeguards without treating every event as another charge.
  [Ingestion and audit](phase-1.md)
- **Recommendation and eligibility.** A rule proposes a retry. An independent eligibility
  check verifies recorded evidence, consent, approval, cooldown and budget before execution.
  The worker uses a durable simulator receipt to reconcile uncertain results. PostgreSQL
  owns the action and outcome. Redis transports work, so redelivery can be handled without
  relying on exactly-once message delivery. [Recovery state machine](phase-3.md)
- **Features and diagnosis.** A feature snapshot is linked to the organization and source
  payment event. The model receives an explicit feature allowlist. Latency is available
  after a response, which fits diagnosis of a completed failure. Specific reported causes
  take precedence. Ambiguous failures can use the fitted model or abstain.
  [Feature contract](phase-4a.md) · [Diagnosis](phase-4c.md)
- **Training and validation.** Fit preprocessing and models on January, choose the candidate
  and threshold on February, and evaluate on March and a later distinct-seed cohort.
  Macro F1 gives rare causes equal weight. Accepted accuracy needs coverage next to it,
  because abstaining on difficult cases can raise accuracy while serving fewer payments.
  [Temporal comparison and SHAP](phase-4c.md)

## Results you can cite accurately

| Evaluation | Result | Scope |
| --- | --- | --- |
| Degradation replay | Both rules detected 3/3 incidents within 24 hours in each incident run. Fixed produced 3 false alerts across both runs versus 16 for rolling. | The two synthetic runs shared the same incident schedule. |
| Failure diagnosis | March macro F1 was 0.307. The forest accepted 244/471 ambiguous failures and was 78.7% accurate among accepted cases. Risk-block F1 was zero. | Synthetic temporal evaluation. SHAP explains model scores and depends on the background and masking assumptions. |
| Monetary simulation | Fixed retry averaged ₹43,448.11 net incremental per 500-customer cohort, with a 95% seed-bootstrap interval of ₹41,464.47–₹45,451.16. Reason-based retry averaged ₹19,087.24. | 100 matched seeds under one scenario. Faster readiness reduced the fixed policy's advantage to ₹1.65 per cohort. |

The dashboard seed uses a smaller monetary experiment than the 100-seed evaluation.
Use the seed count displayed on the dashboard when discussing its figures.
[Detection](phase-4b.md) · [Diagnosis](phase-4c.md) · [Monetary experiment](phase-5.md)

## Current scope

The app runs on loopback with synthetic data and simulator execution. Detection, model
evaluation and SHAP feed the dashboard through offline reports. Consent and eligibility
use the existing deterministic recovery path. External deployment would require
authentication, authorization, and an explicit provider sandbox decision. Live payment
behavior and real-world recovery lift would require independent evidence.
