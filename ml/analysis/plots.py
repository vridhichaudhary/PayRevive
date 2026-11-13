from collections import defaultdict
from datetime import timedelta
from pathlib import Path


def render_audit(data, truth, selected, summary: dict, output: Path) -> None:
    # This is a batch renderer, not a Jupyter or GUI dependency.
    import os
    import tempfile

    os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "payrevive-mpl"))
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.dates import DateFormatter
    from matplotlib.figure import Figure
    from matplotlib.ticker import PercentFormatter

    figure = Figure(figsize=(13, 8), dpi=140, layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(2, 2)
    population = summary["population"]
    axes[0, 0].bar(
        ["Succeeded", "Failed"],
        [population["explored_successes"], population["explored_failures"]],
        color=["#247c77", "#ad423d"],
    )
    axes[0, 0].set_title("Initial observed outcomes — exploratory slice")
    axes[0, 0].set_ylabel("Payments")
    classes = summary["class_distribution"]
    axes[0, 1].bar(
        [row["category"].replace("_", "\n") for row in classes],
        [row["count"] for row in classes],
        color="#396c99",
    )
    axes[0, 1].set_title("Hidden evaluation labels — never classifier inputs")
    axes[0, 1].set_ylabel("Failed initial attempts")
    by_segment = defaultdict(list)
    for index in selected:
        feature = data.features[index]
        by_segment[feature.segment].append(feature.amount_minor / 100)
    segments = sorted(by_segment)
    axes[1, 0].boxplot(
        [by_segment[name] for name in segments], tick_labels=segments, showfliers=False
    )
    axes[1, 0].set_title("Recurring amounts remain coupled to segment")
    axes[1, 0].set_ylabel("Amount (INR; display conversion only)")
    missing = [row for row in summary["feature_statistics"] if row["missing_count"]]
    if missing:
        axes[1, 1].bar(
            [row["feature"] for row in missing],
            [row["missing_count"] for row in missing],
            color="#a87528",
        )
    axes[1, 1].set_title("Missing candidate inputs (empty history is expected)")
    axes[1, 1].set_ylabel("Observations")
    for axis in axes.ravel():
        axis.grid(axis="y", alpha=0.15)
    figure.suptitle("PayRevive data audit — synthetic observations, no model fitted", fontsize=14)
    figure.savefig(output / "overview.png", metadata={"Software": "PayRevive audit v1"})

    temporal = Figure(figsize=(13, 7), dpi=140, layout="constrained")
    FigureCanvasAgg(temporal)
    temporal_axes = temporal.subplots(2, 1)
    colors = ["#246b8d", "#a44a3f", "#2b8070"]
    first = data.features[selected[0]].observed_at.date()
    last = data.features[selected[-1]].observed_at.date()
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    for axis, scope in zip(temporal_axes, ["issuer", "gateway"], strict=True):
        totals, failures = defaultdict(int), defaultdict(int)
        for index in selected:
            feature, attempt = data.features[index], data.attempts[index]
            key = (getattr(feature, scope), feature.observed_at.date())
            totals[key] += 1
            failures[key] += attempt.status == "failed"
        segments = sorted({segment for segment, _ in totals})
        for color, segment in zip(colors, segments, strict=False):
            rates = [
                failures[(segment, day)] / totals[(segment, day)]
                if totals[(segment, day)]
                else float("nan")
                for day in days
            ]
            axis.plot(days, rates, label=segment, color=color, linewidth=1.3)
        for incident in truth.incidents:
            if incident.scope == scope and incident.starts_at.date() <= last:
                axis.axvspan(
                    incident.starts_at.date(),
                    min(incident.ends_at.date(), last + timedelta(days=1)),
                    color="#d9b44a",
                    alpha=0.16,
                )
        axis.set_title(
            f"Daily observed failure rate by {scope}; shaded injected windows", loc="left"
        )
        axis.set_ylim(0, 1)
        axis.set_ylabel("Failure rate")
        axis.yaxis.set_major_formatter(PercentFormatter(1))
        axis.xaxis.set_major_formatter(DateFormatter("%d %b"))
        axis.grid(alpha=0.15)
        axis.legend(loc="upper right")
    temporal.suptitle(
        "Exploratory time series — gaps mean no attempts, not zero failures", fontsize=13
    )
    temporal.savefig(output / "temporal.png", metadata={"Software": "PayRevive audit v1"})
