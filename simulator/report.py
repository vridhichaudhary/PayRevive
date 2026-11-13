"""Descriptive diagnostics against known simulator truth, not financial evaluation."""

import os
import tempfile
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from simulator.models import Dataset


def incident_diagnostics(data: Dataset, baseline: Dataset) -> list[dict]:
    if len(data.payments) != len(baseline.payments):
        raise ValueError("Paired cohorts must have equal length")
    for a, b, af, bf in zip(
        data.payments, baseline.payments, data.features, baseline.features, strict=True
    ):
        if (a.due_at, a.amount_minor, af.issuer, af.network, af.gateway) != (
            b.due_at,
            b.amount_minor,
            bf.issuer,
            bf.network,
            bf.gateway,
        ):
            raise ValueError("Paired cohorts do not match")
    rows = []
    for incident in data.incidents:
        indices = [
            i
            for i, label in enumerate(data.labels)
            if incident.incident_id in label.exposed_incident_ids
        ]
        n = len(indices)
        failures = sum(data.attempts[i].status == "failed" for i in indices)
        baseline_failures = sum(baseline.attempts[i].status == "failed" for i in indices)
        rows.append(
            {
                **incident.model_dump(mode="json"),
                "attempts": n,
                "failures": failures,
                "baseline_failures": baseline_failures,
                "failure_rate": failures / n if n else None,
                "paired_no_incident_rate": baseline_failures / n if n else None,
                "simulated_rate_difference": (failures - baseline_failures) / n if n else None,
                "low_sample_warning": n < 60,
            }
        )
    return rows


def summarize(data: Dataset, baseline: Dataset) -> dict:
    failures = sum(a.status == "failed" for a in data.attempts)
    return {
        "dataset_id": data.dataset_id,
        "synthetic_only": True,
        "customers": len(data.customers),
        "payment_methods": len(data.methods),
        "payments": len(data.payments),
        "initial_attempts": len(data.attempts),
        "failed_attempts": failures,
        "failure_rate": failures / len(data.attempts),
        "failure_categories": dict(
            sorted(
                Counter(
                    label.true_failure_category
                    for label in data.labels
                    if label.true_failure_category
                ).items()
            )
        ),
        "failures_without_reported_reason": sum(
            a.status == "failed" and a.reason is None for a in data.attempts
        ),
        "incidents": incident_diagnostics(data, baseline),
        "interpretation": "Synthetic incident sanity check; no recovery or real-world claim.",
    }


def render_incidents(data: Dataset, baseline: Dataset, output: Path) -> None:
    # Avoid writing font caches into the user home directory. Respect explicit overrides.
    os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "payrevive-mpl"))
    # Figure/Agg avoids GUI dependencies and global pyplot state; all styles are explicit.
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.dates import DateFormatter
    from matplotlib.figure import Figure
    from matplotlib.ticker import PercentFormatter

    panels = list(data.incidents) or [None]
    figure = Figure(figsize=(12, 3.1 * len(panels)), dpi=140, layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(len(panels), 1, squeeze=False).ravel()
    for axis, incident in zip(axes, panels, strict=True):
        counts, failed, control = defaultdict(int), defaultdict(int), defaultdict(int)
        for attempt, base, feature in zip(
            data.attempts, baseline.attempts, data.features, strict=True
        ):
            if incident is not None and getattr(feature, incident.scope) != incident.target:
                continue
            day = attempt.occurred_at.date()
            counts[day] += 1
            failed[day] += attempt.status == "failed"
            control[day] += base.status == "failed"
        first = data.payments[0].due_at.date()
        last = data.payments[-1].due_at.date()
        days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
        rates = [failed[d] / counts[d] if counts[d] else float("nan") for d in days]
        control_rates = [control[d] / counts[d] if counts[d] else float("nan") for d in days]
        axis.plot(
            days,
            rates,
            color="#b83232",
            linewidth=1.6,
            label="Incident scenario",
        )
        axis.plot(
            days,
            control_rates,
            color="#216b9b",
            linewidth=1.2,
            linestyle="--",
            label="Same cohort, incidents disabled",
        )
        if incident is not None:
            axis.axvspan(
                incident.starts_at,
                incident.ends_at,
                color="#efb244",
                alpha=0.22,
                label="Injected incident window",
            )
            title = f"{incident.incident_id}: {incident.scope} = {incident.target}"
        else:
            title = "All initial payment attempts — incidents disabled"
        axis.set_title(title, loc="left", fontsize=11)
        axis.set_ylabel("Daily failure rate")
        axis.yaxis.set_major_formatter(PercentFormatter(1))
        axis.xaxis.set_major_formatter(DateFormatter("%d %b"))
        axis.set_ylim(0, 1)
        axis.grid(alpha=0.18)
        axis.legend(loc="upper right", fontsize=8)
    figure.suptitle(
        "PayRevive: injected degradation in synthetic subscription payments", fontsize=14
    )
    figure.savefig(output, metadata={"Software": "PayRevive simulator 1.0.0"})
