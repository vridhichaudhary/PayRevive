# Phase 4C — Failure diagnosis and SHAP

PayRevive can diagnose a completed synthetic payment failure from a specific
reported reason or from a classifier score when the reason is ambiguous.
The diagnosis function returns the reason, source, and score when applicable.
Its outputs are advisory. Recovery continues to enforce its own policy and consent.

## Feature and label boundaries

A reason such as `expired_method` is returned directly. A missing reason or
`unknown` reaches the model. The model can abstain if its highest scoring
category is unknown or if its score falls below the threshold. If validation
finds no qualifying threshold, ambiguous cases always abstain.

The data loader validates the source manifest and joins features to payment
events. The experiment filters to ambiguous completed failures, then joins the
evaluator's hidden cause by payment reference. The feature allowlist includes
post-response latency, days to expiry, amount, calendar and routing context,
segment, and tenure. IDs, status, reported reason, incident truth, and the
target are outside the input frame. Earlier-payment history is excluded
because every January observation starts without history. Date-derived
features should be checked for calendar effects as the generator evolves.

## How the models are assessed

The reference corpus uses seed 42 across January, February and March 2026.
January fits a majority baseline, logistic regression and a random forest.
The learned candidates fit one-hot encoding and numeric preprocessing on
January only. Logistic regression represents additive relationships in the
logits. The forest can capture interactions and nonlinear effects, such as
a sharp change around card expiry.

February chooses the learned candidate with the best macro F1, then picks the
highest-coverage threshold from a fixed grid subject to 70% accepted accuracy
and 10% ambiguous coverage. March measures the frozen models and rule. The
seed-71 challenge evaluates May 2026 with different generated customers and a
shifted calendar. The same generator creates both datasets. The March cohort
also shares customer profiles with January, so the distinct-seed challenge is
a separate check on new simulated customers. These are temporal demonstrations
on the existing corpus and simulator family.

Macro F1 gives equal weight to the six categories. Keep class counts and the
confusion matrix nearby to see which errors drive it. Log loss evaluates the
full predicted class distribution. Coverage measures the fraction of ambiguous
failures the model diagnoses. Accepted accuracy counts correct diagnoses among
that fraction. The numeric threshold is a model score, and the validation
accuracy criterion is an empirical operating point. It provides no statistical
guarantee for later cohorts.

## Measured results

| Model | February macro F1 | March macro F1 | Challenge macro F1 |
| --- | ---: | ---: | ---: |
| Majority baseline | 0.082 | 0.077 | 0.085 |
| Logistic regression | 0.281 | 0.277 | 0.305 |
| Random forest | 0.322 | 0.307 | 0.308 |

The forest was selected on February with a threshold of 0.50. On March it
accepted 244 of 471 ambiguous failures and diagnosed 192 of those correctly
(78.7%). It abstained on 227 ambiguous failures. Another 736 failed payments
had specific reported reasons. On the challenge it accepted 177 of 430 and
diagnosed 142 correctly (80.2% accepted accuracy, 41.2% coverage).

March F1 was 0.874 for expiry and 0.621 for insufficient funds. Gateway,
issuer, and unknown had F1 below 0.15, and risk-block F1 was zero. These errors
matter even where accepted accuracy looks stronger. Repeated customers,
generator-defined causes and limited scenario diversity constrain the claims.
A live provider would require independent verification of labels, calibration,
and error rates.

## Explain one prediction

The report uses [Permutation SHAP](https://shap.readthedocs.io/en/latest/generated/shap.PermutationExplainer.html)
on the full probability pipeline. Inputs remain at their original feature
granularity. For example, issuer is one feature even though the pipeline
uses multiple one-hot columns. The explainer draws 32 background rows from
January and explains a seeded sample of 24 March rows. It checks additivity
against every output probability on those rows.

In the reference example, expired-method probability rises from a background
average of 22.6% to 70.2%. Days to expiry contributes about 42.6 percentage
points, with a card 32 days past its expiry date in this example. The model's
probability reconstruction error was below 1e-8. The attribution describes
the fitted model's response to its inputs and background distribution.
Correlated inputs and masked combinations affect how contributions are
assigned, and causal claims require separate evidence.

## Reproduce the notebook

Use the locked analysis group from the repository root and choose new output
directories.

```sh
uv sync --frozen --group analysis
uv run --group analysis python -m simulator.run --config simulator/configs/baseline.json --output work/diagnosis/reference
uv run --group analysis python -m simulator.run --config simulator/configs/detection-challenge.json --output work/diagnosis/challenge
uv run --group analysis python -m ml.notebooks.run \
  --notebook notebooks/03_failure_diagnosis.ipynb \
  --dataset work/diagnosis/reference --challenge work/diagnosis/challenge \
  --output work/diagnosis/report
```

Open `work/diagnosis/report/report.html` in a browser. The JSON includes
counts, scores, the threshold sweep, SHAP contributions, package versions,
source hashes and verified dataset provenance. Use `python -m ml.analysis.diagnosis`
with the same dataset and challenge paths for the shared CLI. The notebook
is an explanatory client of these modules, and serving dependencies remain
independent of the analysis group.

Next comes the monetary policy experiment. It will compare policies over
matched simulated cohorts and quantify uncertainty in incremental recovery.
