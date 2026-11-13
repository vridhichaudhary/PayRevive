"""Reusable data-audit implementation. CLI and notebooks call the same function."""

import argparse
import json
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from app.features.contracts import (
    FEATURE_RATIONALES,
    FEATURE_SCHEMA_VERSION,
    MODEL_INPUT_FIELDS,
    FeatureValues,
)
from pydantic import AwareDatetime, TypeAdapter

from ml.analysis.plots import render_audit
from ml.analysis.provenance import environment_record
from ml.data.evaluation import load_truth
from ml.data.loading import OperationalDataset, load_operational
from simulator.config import month_start
from simulator.generator import canonical
from simulator.models import Reason


def summarize(data: OperationalDataset, truth, until: datetime) -> tuple[dict, list[int]]:
    start = datetime.combine(data.config.start, datetime.min.time(), UTC)
    end = datetime.combine(
        month_start(data.config.start, data.config.months), datetime.min.time(), UTC
    )
    if until.tzinfo is None or not start < until <= end:
        raise ValueError("Exploration cutoff must be aware and within the observation period")
    selected = [i for i, f in enumerate(data.features) if f.observed_at < until]
    if not selected:
        raise ValueError("No observations before the exploration cutoff")
    failures = sum(data.attempts[i].status == "failed" for i in selected)
    class_counts = Counter(
        truth.labels[i].true_failure_category
        for i in selected
        if truth.labels[i].true_failure_category is not None
    )
    monthly = Counter(data.features[i].observed_at.strftime("%Y-%m") for i in selected)
    statistics, catalog = [], []
    for name in MODEL_INPUT_FIELDS:
        values = [getattr(data.features[i], name) for i in selected]
        present = [value for value in values if value is not None]
        numeric = FeatureValues.model_fields[name].annotation is not str
        statistics.append(
            {
                "feature": name,
                "missing_count": len(values) - len(present),
                "missing_fraction": (len(values) - len(present)) / len(values),
                "distinct_values": len(set(present)),
                "minimum": min(present) if numeric and present else None,
                "maximum": max(present) if numeric and present else None,
            }
        )
        catalog.append(
            {
                "feature": name,
                "role": "candidate, not yet selected by validation",
                "availability": "after_response"
                if name == "gateway_latency_ms"
                else "before_or_at_observation",
                "rationale": FEATURE_RATIONALES[name],
            }
        )
    population = {
        "customers_total": len(data.customers),
        "methods_total": len(data.methods),
        "initial_observations_total": len(data.features),
        "explored_observations": len(selected),
        "withheld_observations": len(data.features) - len(selected),
        "explored_failures": failures,
        "explored_successes": len(selected) - failures,
        "exploratory_failure_rate": failures / len(selected),
        "failures_without_reported_reason": sum(
            data.attempts[i].status == "failed" and data.attempts[i].reason is None
            for i in selected
        ),
    }
    summary = {
        "audit_version": "audit-v1",
        "synthetic_only": True,
        "final_test_claim": False,
        "provenance": {
            "dataset_id": data.manifest.dataset_id,
            "source_manifest_sha256": data.manifest_sha256,
            "generator_version": data.manifest.generator_version,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "explore_until_exclusive": until.astimezone(UTC).isoformat(),
        },
        "population": population,
        "monthly_explored_counts": dict(sorted(monthly.items())),
        "class_distribution": [
            {
                "category": category,
                "count": class_counts[category],
                "fraction_of_failures": class_counts[category] / failures if failures else None,
            }
            for category in get_args(Reason)
        ],
        "feature_statistics": statistics,
        "feature_catalog": catalog,
        "excluded_join_metadata": ["payment_ref", "source_event_id", "org_id", "observed_at"],
        "never_model_inputs": [
            "true_failure_category",
            "exposed_incident_ids",
            "reason",
            "status",
            "recovery_outcomes",
            "source_manifest_sha256",
        ],
        "interpretation": "Exploratory synthetic associations; no model or detector evaluated.",
        "environment": environment_record(),
    }
    return summary, selected


def run_audit(
    directory: Path,
    output: Path,
    until: datetime | str | None = None,
    expected_manifest_sha256: str | None = None,
) -> dict:
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    if isinstance(until, str):
        until = TypeAdapter(AwareDatetime).validate_python(until)
    data = load_operational(directory)
    if expected_manifest_sha256 is not None and data.manifest_sha256 != expected_manifest_sha256:
        raise ValueError("Notebook dataset does not match the declared source manifest")
    truth = load_truth(data)
    until = until or datetime.combine(
        month_start(data.config.start, data.config.months - 1), datetime.min.time(), UTC
    )
    summary, selected = summarize(data, truth, until)
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".payrevive-audit-", dir=output.parent) as temporary:
        stage = Path(temporary)
        render_audit(data, truth, selected, summary, stage)
        (stage / "audit.json").write_bytes(canonical(summary))
        stage.rename(output)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify and audit a real generated dataset")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--until", help="Exclusive aware UTC/calendar cutoff")
    args = parser.parse_args()
    summary = run_audit(args.dataset, args.output, args.until)
    print(
        json.dumps(
            {"provenance": summary["provenance"], "population": summary["population"]}, indent=2
        )
    )


if __name__ == "__main__":
    main()
