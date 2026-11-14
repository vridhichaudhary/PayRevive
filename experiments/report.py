"""Run matched seed replays and export paired bootstrap intervals."""

import argparse
import csv
import hashlib
import random
import statistics
import tempfile
from pathlib import Path

from app.compliance import engine
from app.decision import policy
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from experiments import replay
from ml.analysis.provenance import environment_record
from simulator import generator
from simulator.generator import canonical

CONTRASTS = (
    ("fixed", "control"),
    ("reason_based", "control"),
    ("reason_based", "fixed"),
)


def summarize(rows: list[dict], config: replay.ExperimentConfig) -> list[dict]:
    rng = random.Random(config.bootstrap_seed)
    # The same sampled seed indices are used across outcomes and policy contrasts.
    indices = [rng.choices(range(len(rows)), k=len(rows)) for _ in range(config.bootstrap_draws)]
    summaries = []
    for treatment, reference in CONTRASTS:
        for metric in ("gross_minor", "cost_minor", "net_incremental_minor"):
            differences = [
                row["policies"][treatment][metric] - row["policies"][reference][metric]
                for row in rows
            ]
            draws = sorted(statistics.fmean(differences[i] for i in sample) for sample in indices)
            quantiles = statistics.quantiles(draws, n=40, method="inclusive")
            low, high = quantiles[0], quantiles[-1]
            summaries.append(
                {
                    "policy": treatment,
                    "comparison": reference,
                    "metric": metric,
                    "mean_difference_paise_per_cohort": statistics.fmean(differences),
                    "ci95_low_paise": low,
                    "ci95_high_paise": high,
                }
            )
    return summaries


def plot(summary: list[dict], path: Path) -> None:
    rows = [row for row in summary if row["metric"] == "net_incremental_minor"]
    figure = Figure(figsize=(9, 3.4), layout="constrained")
    FigureCanvasAgg(figure)
    axis = figure.subplots()
    for index, row in enumerate(rows):
        low, high = row["ci95_low_paise"] / 100, row["ci95_high_paise"] / 100
        axis.hlines(index, low, high, color="#116773", linewidth=2)
        axis.scatter(row["mean_difference_paise_per_cohort"] / 100, index, color="#116773")
    axis.axvline(0, color="#777777", linestyle="--", linewidth=1)
    axis.set(
        yticks=range(len(rows)),
        yticklabels=[f"{r['policy'].replace('_', ' ')} vs {r['comparison']}" for r in rows],
        xlabel="Mean net recovery difference per cohort (INR)",
        title="Matched recovery replay · 95% seed-bootstrap intervals",
    )
    figure.savefig(path, dpi=145)


def run(config: replay.ExperimentConfig, output: Path) -> dict:
    output = output.absolute()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    rows = []
    for seed in range(config.first_seed, config.first_seed + config.runs):
        dataset_id, cases = replay.build_cases(seed, config)
        rows.append(
            {"seed": seed, "dataset_id": dataset_id, "policies": replay.compare(cases, config)}
        )
    summary = summarize(rows, config)
    environment = environment_record()
    for name, file in (
        ("experiments.report", __file__),
        *((module.__name__, module.__file__) for module in (replay, generator, engine, policy)),
    ):
        environment["source_module_sha256"][name] = hashlib.sha256(
            Path(file).read_bytes()
        ).hexdigest()
    result = {
        "version": replay.EXPERIMENT_VERSION,
        "synthetic_only": True,
        "config": config.model_dump(mode="json"),
        "rules_version": config.rules.version,
        "environment": environment,
        "seeds": rows,
        "contrasts": summary,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".payrevive-experiment-", dir=output.parent
    ) as temporary:
        stage = Path(temporary)
        (stage / "config.json").write_bytes(canonical(result["config"]))
        (stage / "results.json").write_bytes(canonical(result))
        with (stage / "summary.csv").open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(summary[0]))
            writer.writeheader()
            writer.writerows(summary)
        plot(summary, stage / "comparison.png")
        manifest = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(stage.iterdir())
        }
        (stage / "manifest.json").write_bytes(canonical(manifest))
        stage.rename(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="ExperimentConfig JSON")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = (
        replay.ExperimentConfig.model_validate_json(args.config.read_bytes())
        if args.config
        else replay.ExperimentConfig()
    )
    result = run(config, args.output)
    for row in result["contrasts"]:
        if row["metric"] == "net_incremental_minor":
            mean, lo, hi = (
                row[key] / 100
                for key in ("mean_difference_paise_per_cohort", "ci95_low_paise", "ci95_high_paise")
            )
            print(
                f"{row['policy']} vs {row['comparison']} | "
                f"INR {mean:.2f} [{lo:.2f}, {hi:.2f}] per cohort"
            )


if __name__ == "__main__":
    main()
