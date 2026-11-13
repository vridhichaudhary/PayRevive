# Synthetic subscription ecosystem — dataset card

Generator version 1.0.0. Entirely synthetic, INR card subscriptions, one explicit organization.
No real customers, bank statistics, provider codes, card numbers, or CVVs are used.

## Population and mechanisms

The default is 6,000 customers with three monthly obligations each, January–March 2026.
Customers have budget/standard/premium segments, stable subscription amounts and billing
days, tenure, and linked card metadata. Issuer/network distributions are coupled. Occasional
plan changes alter recurring amounts. Private liquidity heterogeneity, amount changes,
month-end timing, expiry metadata, issuer differences, and injected incidents influence
failure-category hazards. The total hazard determines failure probability; overlapping
weighted causes determine the hidden category of failed attempts.

Three incidents temporarily increase issuer/gateway hazards. Payment cohorts and random
outcome draws are shared across otherwise identical incident-strength variants, permitting
a paired simulator sanity check. Failure categories and every probability are hypothetical
simulation assumptions, not measured financial behavior or external compliance rules.

The expiry field is noisy/stylized metadata: an expired metadata date raises failure risk
but does not deterministically enforce a processor decline. This is a modeling limitation,
not evidence that real expired instruments can be charged. Failure observations and execution
eligibility must remain separate; future policy must honor explicit terminal decline evidence.

## Operational data versus truth

Observable customers, safe method metadata, payments, attempts, and feature snapshots are
separate from hidden failure labels and incident membership. A missing reported reason can
coexist with a known hidden synthetic label. Actual reason labels must not be classifier inputs.
`Features.model_inputs()` explicitly allowlists inputs and excludes identifiers, raw timestamps,
status, reported reasons, hidden incident flags, and recovery outcomes. Feature history includes
only strictly earlier observed payments; the current observation is appended afterward.
Gateway latency is available at failure diagnosis time, not before a payment is attempted.

## Intended use and limitations

Use for pipeline verification, incident-detection development, temporally validated diagnosis,
and later recovery-simulator inputs. The present generator emits one initial attempt per
obligation. It does not fabricate retry histories, recovery outcomes, policy lift, or revenue.
Those must result from future policy/execution experiments.

Patterns are hand-designed and do not establish realism or real-world generalization.
A held-out seed is not a held-out generating process. Later ML evaluation must include changed
scenario parameters, temporal splits, baselines, class-level metrics, and leakage audits.
No passing sanity check proves that every possible leakage path or simple predictor is absent.
Customers currently have one card and remain on the same plan except occasional amount changes.
No refunds, partial captures, settlement, foreign exchange, real consent, or provider behavior
is modeled. Feature values are synthetic operator metadata, not inferred facts about people.

## Reproducibility

Configuration, generator version, seeded semantic random streams, and deterministic ordering
identify a dataset. Defaults are historical to remain compatible with simulator ingestion.
Changing a configuration changes record IDs to prevent silent reuse of source event identities.
The same software/configuration must reproduce the same data; generator or Python/library
changes require re-verification. See [Phase 2](phase-2.md) for canonical exports, checksums, plots, ingestion, and actual
acceptance results. Observable profiles/features remain offline; only payment/event contracts
are currently persisted by the adapter. This boundary must be extended before online diagnosis.


## Validated default export

The seed-42 default has 18,000 initial attempts and 3,361 failures, spread across all six
hidden failure categories. Of those failures, 1,151 have no reported reason. Three injected
incidents are visible in the daily rate chart and have positive paired failure differences.
These numbers describe version-1 simulation output, not industry statistics. Missing reasons
are masked randomly among failed attempts; real missingness may be systematic. Profiles are
fixed baseline metadata for the scenario, including tenure measured at the population start.
See the Phase 2 guide for actual checks and limitations.
