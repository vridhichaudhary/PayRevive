# PayRevive analysis notebooks

These are executable explanations of shared Python modules. They are not the implementation
of data loading, features, detection, training, evaluation, or business policy.

## Current notebook

`01_data_and_feature_audit.ipynb` verifies real simulator artifacts, explains candidate feature
availability/leakage boundaries, and inspects actual distributions and injected incidents.
No model is fitted and no detector metric or SHAP explanation is claimed.

## Open interactively

From the repository root:

```sh
uv sync --frozen --group analysis
uv run --group analysis python -m simulator.run --output work/datasets/demo
uv run --group analysis jupyter lab notebooks/01_data_and_feature_audit.ipynb
```

If the dataset directory already exists, reuse it or choose another name; generation never
overwrites a run. In VS Code, open the notebook and select the project's `.venv` Python kernel.
Inspect the Python path printed at the end. The default input path is relative to the installed
project module, not the notebook's working directory. Interactive outputs receive a new scratch
folder under `work/analysis/`; that folder name does not influence data or statistics.

## Reproduce headlessly — recommended for review

```sh
uv run --group analysis python -m ml.notebooks.run --dataset work/datasets/demo --output work/analysis/data-audit
```

Open `work/analysis/data-audit/report.html` without Jupyter, or open `executed.ipynb`. That
folder also contains `audit/audit.json`, two genuine PNG figures, and `run_manifest.json`.
Output directories must be new. The runner binds the source manifest, explicitly selects the
current Python executable, starts a fresh private kernel, clears stale outputs, executes in
order, and closes both kernel and client resources. On macOS/Linux it uses local IPC sockets;
Windows falls back to loopback TCP. Runtime/config/cache files are temporary and not published.

For a report without notebook execution:

```sh
uv run --group analysis python -m ml.analysis.audit --dataset work/datasets/demo --output work/analysis/audit-cli
```

Both paths call `run_audit`; there is no second metrics or plotting implementation.
`--until` accepts an aware ISO timestamp and is exclusive. By default the final generated
month is withheld from displayed outcome statistics. The seed-42 corpus has already been
explored and is not an untouched final model test set. Later model selection must reserve
appropriate data and freeze its validation protocol.

## Source and results policy

Commit notebook source with cleared outputs. Publish executed notebooks/HTML through the
CI `data-audit-notebook` artifact, which uses a 300-customer real generated smoke dataset.
The full local/default report uses 6,000 customers. Dataset identity/configuration is recorded
so these reports cannot be mistaken for one another. Small reviewed findings can be documented
in `docs/experiment_reports/`; raw generated records and large execution artifacts stay out of Git.

Reusable exploratory code must move into a tested module when retained; replace the cell
with an import/call. Keep meaningful Markdown questions, conclusions, alternatives, and limits.
Training/serving must remain independent of notebook kernels. Jupyter/pandas tooling belongs
to the optional `analysis` group and is explicitly checked absent from the serving image.

## Planned notebooks

Degradation detection, then diagnosis/model comparison/SHAP, will be added with their actual
working modules and evaluations. A financial-experiment notebook follows the experiment engine.
There are no placeholder notebooks or fabricated results for those future milestones.

## Degradation detection

Open `02_degradation_detection.ipynb` from a kernel with the locked analysis
environment. Set `PAYREVIVE_NOTEBOOK_DATASET` to the dataset directory when using
an export other than the default `work/datasets/demo`. Follow the
[degradation guide](../docs/phase-4b.md) for the fresh-kernel command, comparison
scenarios, and actual results. CI publishes the executed smoke artifact as
`degradation-detection-notebook`.

## Failure diagnosis

Open `03_failure_diagnosis.ipynb` in the locked analysis environment. The
[diagnosis guide](../docs/phase-4c.md) gives the fresh-kernel command and
explains the temporal comparison, abstention, and SHAP results. CI publishes
an executed smoke report as `failure-diagnosis-notebook`.
