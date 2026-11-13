# Exploratory data audit — generator v1, seed 42

Dataset: `05c4b78b912238e4`; source manifest SHA-256:
`9701b5751b82f227ad825fe12524fd6c94c7d233ef7e2ff32b41e941bc84eee4`.
The input was genuinely generated and checked by the loader. This is descriptive EDA,
not a model/detector evaluation or an untouched final test set.

| Quantity | Actual value |
| --- | ---: |
| Customers/methods | 6,000 each |
| Initial observations in corpus | 18,000 |
| Explored before 1 March 2026 UTC | 12,000 |
| March observations withheld from displayed outcomes | 6,000 |
| Explored successes | 9,846 |
| Explored failures | 2,154 (17.95%) |
| Failures without a reported reason | 733 |
| Missing prior success rate | 6,000 (first monthly observation per customer) |

| Hidden exploratory failure category | Count |
| --- | ---: |
| Insufficient funds | 790 |
| Expired method | 689 |
| Issuer unavailable | 224 |
| Gateway error | 183 |
| Risk block | 147 |
| Unknown | 121 |

These counts suggest class imbalance and an ambiguous-reason workload. They do not select
a model or prove predictive utility. Amount/segment relationships and expiry associations
are deliberately built into the generator. Missing history should not be filled using the
current/future outcome. Only training-fitted preprocessing and validation experiments can
justify feature transformations or model choice.

The charts show injected issuer/gateway windows and gaps on days without attempts. No alert
precision, recall, false-positive rate, model score, or SHAP contribution is claimed here.

Reproduce using the default simulator config and `python -m ml.notebooks.run`, as described
in [the notebook guide](../../notebooks/README.md). CI publishes a real, smaller 300-customer
smoke report under the `data-audit-notebook` artifact; its counts differ and its manifest
identifies that dataset. Full rendered results are delivered separately from committed sources.
