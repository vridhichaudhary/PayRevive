# Phase 4B — Degradation detection

PayRevive can replay initial terminal payment attempts and flag unusual daily decline
rates by organization, issuer, or gateway. This milestone runs offline through the CLI
and a notebook. The API, recovery worker, and database schema are independent of it.

## How it works

`ml/detection/detector.py` buckets attempts by the UTC date of the observed attempt event,
then counts declines and total attempts in each issuer and gateway cohort. A bucket is
scored at its close. Its baseline uses aggregate counts from the preceding 14 calendar
days, so busy days contribute proportionally to their volume. Both rules require at least
20 attempts in the current day and 100 prior attempts. A day without traffic has an
undefined rate, and sparse days remain visible in the export.

- The fixed rule flags a decline fraction of 35% or more.
- The rolling rule requires an increase of at least 10 percentage points and a
  standardized increase of at least 3. The score divides the rate difference by the
  pooled binomial standard error. A zero standard error leaves the score undefined.

The absolute increase helps avoid raising an alert for a change with little operational
impact. Standardization helps avoid treating small and large samples alike. The score
is a heuristic. Outcomes for the same customer can be correlated, many groups are
scanned, and billing cycles can shift the underlying rate. A rolling baseline can adapt
to changing rates, including prolonged degradation. These defaults are illustrative,
and the two rules can be compared before choosing an operating threshold.

The detector reads verified operational data. Once scoring finishes,
`ml/detection/evaluation.py` matches already-issued alerts against separately loaded
incident truth. A matching alert sits in a bucket whose cohort and time intersect the
incident. Delay runs from incident onset to the close of the first flagged bucket.
Each incident contributes to recall, including those with sparse traffic. The false
alert rate uses eligible non-incident cohort-days as its denominator. The JSON report
includes these counts and per-incident records. A gateway shock can raise declines for
an issuer that uses it, so strict target matching can count this kind of spillover as a
false alert.

## Reproduce the experiment

Run from the repository root with Python 3.13 and the locked analysis group. Use new
output paths. The baseline config creates the reference dataset. The challenge and
control configs use the same seed, period, and customers with different incident
strengths. The challenge changes the seed and calendar from the reference, while
preserving the simulator's relative incident schedule and targets.

```sh
uv sync --frozen --group analysis
uv run python -m simulator.run --config simulator/configs/baseline.json --output work/detection/reference
uv run python -m simulator.run --config simulator/configs/detection-challenge.json --output work/detection/challenge
uv run python -m simulator.run --config simulator/configs/detection-control.json --output work/detection/control
uv run --group analysis python -m ml.notebooks.run \
  --notebook notebooks/02_degradation_detection.ipynb \
  --dataset work/detection/reference --output work/detection/notebook
uv run python -m ml.analysis.degradation --dataset work/detection/challenge --output work/detection/challenge-report
uv run python -m ml.analysis.degradation --dataset work/detection/control --output work/detection/control-report
```

Open the notebook run's `report.html` in a browser. The CLI and notebook use the same
report function. It exports daily windows as JSON Lines, metrics and provenance as
JSON, and a timeline that overlays incident truth for analysis. The notebook runner
also records the source notebook hash and checksums of the executed artifacts.

## Results on generated data

Each replay has 18,000 observations. The reference uses seed 42 in January–March
2026. The challenge and the paired control use seed 71 in April–June 2026.
Each scenario has 411 eligible cohort-days. The incident scenarios have 404 eligible
negative windows and the control has 411.

| Scenario | Dataset ID | Rule | Incidents detected | True alert windows | False alert windows | False alert rate |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Reference | `05c4b78b912238e4` | Fixed | 3/3 | 7 | 1 | 0.25% |
| Reference | `05c4b78b912238e4` | Rolling | 3/3 | 7 | 8 | 1.98% |
| Challenge | `6a4d9b57f71697c3` | Fixed | 3/3 | 7 | 2 | 0.50% |
| Challenge | `6a4d9b57f71697c3` | Rolling | 3/3 | 7 | 8 | 1.98% |
| Control | `e988be7231f3e044` | Fixed | — | 0 | 1 | 0.24% |
| Control | `e988be7231f3e044` | Rolling | — | 0 | 3 | 0.73% |

First detection was 24 hours after onset for each injected incident under both rules.
The fixed threshold performed better at the tested operating points. Both injected
scenarios use the same three incident types, timings relative to month start, and
strength. The six detections give a small sample, and overlapping groups and repeated
customers make individual buckets dependent. These measurements apply to this
synthetic replay. Production calibration would require observed incident records and
a broader range of outage strengths, lengths, and traffic conditions.

The next Phase 4 milestone covers per-payment failure diagnosis, temporal model
comparison, and SHAP explanations. Monetary policy experiments remain a separate phase.
