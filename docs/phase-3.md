# Phase 3 — Safe rule-based recovery

Status: policy and deterministic eligibility component implemented; durable execution in progress.

The first component separates recommendation from permission. Known transient/funds failures
can propose a delayed retry; expired metadata proposes a payment-method update, risk evidence
blocks retries, and ambiguous evidence requests review. No messages are sent by these proposals.

The eligibility engine independently checks explicit online consent, current payment status,
terminal evidence, competing actions, attempt cap, age, economics, cooldown, and approval.
Missing rules fail closed. Money comparisons use integer minor units and basis points.

Default simulation assumptions: three total attempts including the original, six-hour cooldown,
24-hour funds delay, six-hour transient delay, seven-day recovery window, nominal 25-paise
execution cost, approval for obligations of INR 1,000 or more. Expected success figures are
configurable simulation assumptions, not trained predictions, network rules, or legal guidance.

Remaining acceptance includes PostgreSQL-persisted recommendations/actions, customer consent,
worker execution, cancellation, stable-operation idempotency, unknown outcome reconciliation,
crash recovery, scheduler/outbox delivery, actual API/worker smoke checks, and documentation.
Do not mark the phase complete while those checks remain outstanding.
