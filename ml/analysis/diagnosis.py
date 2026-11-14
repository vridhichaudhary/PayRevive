"""Train, compare and explain diagnoses on verified synthetic payment outcomes."""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from ml.analysis.provenance import environment_record
from ml.data.evaluation import load_truth
from ml.data.loading import OperationalDataset, load_operational
from ml.diagnosis import experiment, explanations, model
from simulator.config import month_start
from simulator.generator import canonical


def provenance(data: OperationalDataset) -> dict:
    return {
        "source_manifest_sha256": data.manifest_sha256,
        "dataset_id": data.manifest.dataset_id,
        "generator_config": data.config.model_dump(mode="json"),
    }


def render(report: dict, output: Path) -> None:
    example = report["shap"]["example"]
    contributions = sorted(example["contributions"].items(), key=lambda item: abs(item[1]))
    figure = Figure(figsize=(9, 5), layout="constrained")
    FigureCanvasAgg(figure)
    ax = figure.subplots()
    ax.barh(
        [feature for feature, _ in contributions],
        [value for _, value in contributions],
        color=["#176d75" if value >= 0 else "#aa3e54" for _, value in contributions],
    )
    ax.axvline(0, color="#777777", linewidth=1)
    ax.set(
        xlabel="Contribution to predicted probability",
        title=f"SHAP for {example['predicted_class']}\n"
        f"Background {example['base_value']:.1%} → prediction {example['probability']:.1%}",
    )
    figure.savefig(output, dpi=140)


def run_diagnosis(
    directory: Path,
    output: Path,
    expected_manifest_sha256: str | None = None,
    challenge_directory: Path | None = None,
) -> dict:
    output = output.absolute()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    data = load_operational(directory)
    if expected_manifest_sha256 is not None and expected_manifest_sha256 != data.manifest_sha256:
        raise ValueError("Notebook dataset does not match the declared source manifest")
    if data.config.months < 3:
        raise ValueError("Diagnosis requires at least three months for train, validation and test")

    truth = load_truth(data)
    months = [month_start(data.config.start, i) for i in (0, 1, 2)]
    train, validation = [experiment.cohort(data, truth, month) for month in months[:2]]
    models = model.fit_models(train.features, train.labels, data.config.seed)
    comparison = {name: experiment.metrics(fitted, validation) for name, fitted in models.items()}
    selected = max(("logistic", "forest"), key=lambda name: comparison[name]["macro_f1"])
    fitted = models[selected]
    threshold, sweep = experiment.choose_threshold(fitted, validation)
    test = experiment.cohort(data, truth, months[2])
    report = {
        "report_version": "diagnosis-v1",
        "synthetic_only": True,
        "provenance": provenance(data),
        "environment": environment_record(("scikit-learn", "shap", "scipy")),
        "months": dict(
            zip(("train", "validation", "test"), [m.isoformat() for m in months], strict=True)
        ),
        "cohorts": {
            name: {"failed": split.failed, "ambiguous": len(split.labels)}
            for name, split in zip(
                ("train", "validation", "test"), (train, validation, test), strict=True
            )
        },
        "inputs": model.INPUTS,
        "classes": model.CLASSES,
        "validation": comparison,
        "test": {name: experiment.metrics(candidate, test) for name, candidate in models.items()},
        "selected_model": selected,
        "selected_threshold": threshold,
        "validation_thresholds": sweep,
        "test_selective": experiment.selective_metrics(
            fitted.classes_, fitted.predict_proba(test.features), test, threshold
        ),
        "shap": explanations.explain(fitted, train.features, test.features, data.config.seed),
    }
    if challenge_directory is not None:
        challenge = load_operational(challenge_directory)
        if challenge.config.seed == data.config.seed or challenge.config.start <= months[2]:
            raise ValueError("Use a distinct seed and a later calendar for the challenge")
        challenge_month = month_start(challenge.config.start, 1)
        shifted = experiment.cohort(challenge, load_truth(challenge), challenge_month)
        report["challenge"] = {
            "provenance": provenance(challenge),
            "month": challenge_month.isoformat(),
            "comparison": {
                name: experiment.metrics(candidate, shifted) for name, candidate in models.items()
            },
            "selective": experiment.selective_metrics(
                fitted.classes_, fitted.predict_proba(shifted.features), shifted, threshold
            ),
        }
    for name, source in (
        ("ml.analysis.diagnosis", __file__),
        (model.__name__, model.__file__),
        (experiment.__name__, experiment.__file__),
        (explanations.__name__, explanations.__file__),
    ):
        report["environment"]["source_module_sha256"][name] = hashlib.sha256(
            Path(source).read_bytes()
        ).hexdigest()
    # JSON round trip keeps the return value identical to the saved representation.
    encoded = canonical(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".payrevive-diagnosis-", dir=output.parent
    ) as temporary:
        stage = Path(temporary)
        render(report, stage / "shap.png")
        (stage / "diagnosis.json").write_bytes(encoded)
        stage.rename(output)
    return json.loads(encoded)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--challenge", type=Path)
    args = parser.parse_args()
    report = run_diagnosis(args.dataset, args.output, challenge_directory=args.challenge)
    print(
        json.dumps(
            {
                "selected_model": report["selected_model"],
                "threshold": report["selected_threshold"],
                "test_selective": report["test_selective"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
