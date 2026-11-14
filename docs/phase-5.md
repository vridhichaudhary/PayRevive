# Phase 5 — Monetary recovery experiment

The experiment compares three policies on each identical synthetic cohort. The
control applies no PayRevive intervention and can still settle through a shared
background process. Fixed retry waits 48 hours after each failure, subject to
the same minimum delays as the reason-based policy. Reason-based retry uses
the application's existing proposal timing. Both retry policies call the same
eligibility function, apply the same consent and approval draws, charge the
same nominal fee, and stop at the attempt cap or the seven-day horizon.

## Scenario and outcome mechanics

Each seed generates a population of 500 customers over three months. We start
the clock separately for every initially failed payment. The simulator gives
each payment a shared background settlement time with 15% probability, uniform
between one hour and seven days after failure. This stylizes settlement through
a process outside PayRevive. Approval and consent are fixed per simulated
customer, with 75% and 85% probability respectively. A background payment at
or before a scheduled retry wins the tie, so no unnecessary retry is charged
at that time.

The hidden failure cause determines a shared readiness time. Funds failures
can become ready with 60% probability, uniformly between 3 and 96 hours.
Transient issuer and gateway failures can become ready with 85% probability,
uniformly between 0.5 and 30 hours. The other categories have no
retry-readiness time. A retry can settle the payment once it is ready. All
policies see the same latent outcomes for a given payment. A policy sees only
the recorded reason when requesting a retry. The existing rule rejects missing
reasons, terminal evidence and retries without verified consent or any
required approval. The replay holds the initial reported reason constant
across retry failures. Background settlement remains possible if a retry fails.

Entity-keyed streams produce the stochastic inputs before policy evaluation.
A policy that makes more attempts therefore cannot consume randomness from
another policy. The replay measures outcomes without a running API, Celery
worker, or provider. The operational execution simulator has its own receipt
model and remains responsible for durability and idempotency.

## Accounting and uncertainty

All seed-level cash amounts use integer paise. Gross settlement counts each
failed payment once, including background settlements. Incremental gross
subtracts the matched control's gross. Net incremental then subtracts fees
for every intervention retry, including failed attempts. A retry that settles
a payment earlier than the control adds no incremental amount at the common
horizon. The export also reports the amounts settled directly by retries,
the overlap with control settlements, recovery rate by amount, and the share
of retried payments with no incremental settlement.

The 95% percentile intervals resample 100 matched seed-level differences
with replacement, 4,000 times. Each bootstrap draw contains entire cohorts,
preserving dependence between repeated payments from the same customer and
preserving the policy pairing. The estimates describe mean differences per
500-customer simulated cohort. The intervals express seed variation under
the assumed scenario. They do not measure uncertainty in the readiness,
consent, fee, or background-settlement assumptions. The four-seed CI run
checks that the CLI executes and publishes artifacts.

## Baseline results

Averages per cohort across seeds 1000–1099. The mean initial amount at risk
was INR 328,402.63 across about 272 failed payments.

| Policy | Gross settled | Incremental gross vs control | Retry cost | Net incremental vs control |
| --- | ---: | ---: | ---: | ---: |
| Control | INR 49,872.26 | INR 0.00 | INR 0.00 | INR 0.00 |
| Fixed | INR 93,346.79 | INR 43,474.53 | INR 26.42 | INR 43,448.11 |
| Reason-based | INR 68,992.46 | INR 19,120.20 | INR 32.96 | INR 19,087.24 |

Fixed net improvement over control had a 95% interval of INR 41,464.47 to
45,451.16. The reason-based improvement had an interval of INR 17,703.72 to
20,557.12. Fixed exceeded reason-based by INR 24,360.87 on average, with an
interval of INR 22,931.74 to 25,788.89.

The fixed policy gives more time for funds or transient problems to clear
before using the attempt cap. In the sensitivity run we multiply the same
latent readiness delays by 0.25. Fixed then exceeds reason-based by INR
1.65 per cohort (95% interval INR 1.52 to 1.78). Most of the baseline gap
depends on readiness timing. Both runs use the same seeds and cohort sizes.
The report measures settlement by day seven, so it does not value faster
collection or quantify customer impact.

## Reproduce

From the checkout, with new output paths:

```sh
uv sync --frozen
uv run python -m experiments.report --output work/experiments/baseline
uv run python -m experiments.report --config experiments/fast-readiness.json --output work/experiments/fast
```

The output includes exact configuration, seed-level results, paired contrast
CSV, a PNG comparison, and file checksums. Results include source hashes and
the environment. Docker users can also run the CLI inside the rebuilt app
image using a new writable output directory. The next milestone is local
dashboard integration and a cold-start demonstration.
