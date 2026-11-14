"""Reproducible detector replay, evaluation, and visual report."""

import argparse
import hashlib
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from ml.analysis.provenance import environment_record
from ml.data.evaluation import load_truth
from ml.data.loading import load_operational
from ml.detection import detector, evaluation
from simulator.generator import canonical


def render(windows, incidents, output: Path) -> None:
    groups = sorted({(w.scope, w.target) for w in windows})
    figure = Figure(figsize=(12, 2.55 * len(groups)), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(len(groups), 1, sharex=True, squeeze=False)
    for (scope, target), (axis,) in zip(groups, axes, strict=True):
        series = [w for w in windows if (w.scope, w.target) == (scope, target)]
        days = [w.day for w in series]
        axis.plot(days, [w.rate for w in series], label="Observed", color="#147a82")
        axis.plot(days, [w.baseline_rate for w in series], label="Prior 14 days", color="#364e83")
        alerts = [w for w in series if w.rolling_alert]
        axis.scatter(
            [w.day for w in alerts],
            [w.rate for w in alerts],
            label="Rolling alert",
            color="#a12449",
            zorder=3,
        )
        for incident in incidents:
            if (incident.scope, incident.target) == (scope, target):
                axis.axvspan(
                    incident.starts_at.date(), incident.ends_at.date(), color="#e6a732", alpha=0.22
                )
        axis.set(title=f"{scope} / {target}", ylabel="Decline fraction", ylim=(-0.02, 1.03))
    axes[0, 0].legend(loc="upper right", ncols=3)
    figure.suptitle("Daily decline monitoring · shaded bands show injected incidents")
    figure.savefig(output, dpi=130)


def run_detection(
    directory: Path, output: Path, expected_manifest_sha256: str | None = None
) -> dict:
    output = output.absolute()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    data = load_operational(directory)
    if expected_manifest_sha256 and expected_manifest_sha256 != data.manifest_sha256:
        raise ValueError("Notebook dataset does not match the declared source manifest")
    policy = detector.Policy()
    windows = detector.score_windows(data, policy)
    truth = load_truth(data)
    summary = {
        "report_version": "degradation-v1",
        "synthetic_only": True,
        "provenance": {
            "dataset_id": data.manifest.dataset_id,
            "source_manifest_sha256": data.manifest_sha256,
            "generator_config": data.config.model_dump(mode="json"),
        },
        "environment": environment_record(),
        "policy": asdict(policy),
        "evaluation": evaluation.evaluate(windows, truth.incidents),
    }
    for module in (detector, evaluation):
        summary["environment"]["source_module_sha256"][module.__name__] = hashlib.sha256(
            Path(module.__file__).read_bytes()
        ).hexdigest()
    summary["environment"]["source_module_sha256"]["ml.analysis.degradation"] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".payrevive-detection-", dir=output.parent
    ) as temporary:
        stage = Path(temporary)
        render(windows, truth.incidents, stage / "timeline.png")
        (stage / "detection.json").write_bytes(canonical(summary))
        with (stage / "windows.jsonl").open("wb") as file:
            for window in windows:
                row = {
                    **asdict(window),
                    "org_id": str(window.org_id),
                    "day": window.day.isoformat(),
                }
                file.write(canonical(row) + b"\n")
        stage.rename(output)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_detection(args.dataset, args.output)
    print(json.dumps(report["evaluation"], indent=2))


if __name__ == "__main__":
    main()
