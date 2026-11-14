"""Temporal model comparison for failures with missing or generic reasons."""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, log_loss

from ml.data.evaluation import EvaluationTruth
from ml.data.loading import OperationalDataset
from ml.diagnosis.model import CLASSES, decide, feature_frame


@dataclass
class Cohort:
    features: pd.DataFrame
    labels: list[str]
    failed: int


def cohort(data: OperationalDataset, truth: EvaluationTruth, month) -> Cohort:
    labels = {label.payment_ref: label.true_failure_category for label in truth.labels}
    features, targets = [], []
    failed = 0
    for attempt in data.attempts:
        if (attempt.occurred_at.year, attempt.occurred_at.month) != (month.year, month.month):
            continue
        if attempt.status != "failed":
            continue
        failed += 1
        if attempt.reason is None or attempt.reason == "unknown":
            label = labels[attempt.payment_ref]
            assert label is not None
            features.append(data.features_by_ref[attempt.payment_ref])
            targets.append(label)
    if not targets:
        raise ValueError(f"No ambiguous failures for {month:%Y-%m}")
    return Cohort(feature_frame(features), targets, failed)


def metrics(model, cohort: Cohort) -> dict:
    probabilities = model.predict_proba(cohort.features)
    predicted = model.classes_[np.argmax(probabilities, axis=1)]
    aligned = np.zeros((len(predicted), len(CLASSES)))
    for index, label in enumerate(model.classes_):
        aligned[:, CLASSES.index(label)] = probabilities[:, index]
    return {
        "n": len(predicted),
        "accuracy": accuracy_score(cohort.labels, predicted),
        "macro_f1": f1_score(
            cohort.labels, predicted, labels=list(CLASSES), average="macro", zero_division=0
        ),
        "log_loss": log_loss(cohort.labels, aligned, labels=list(CLASSES)),
        "per_class_f1": dict(
            zip(
                CLASSES,
                f1_score(
                    cohort.labels, predicted, labels=list(CLASSES), average=None, zero_division=0
                ).tolist(),
                strict=True,
            )
        ),
        "class_support": {label: cohort.labels.count(label) for label in CLASSES},
        "confusion_matrix": confusion_matrix(
            cohort.labels, predicted, labels=list(CLASSES)
        ).tolist(),
    }


def selective_metrics(classes, probabilities, cohort: Cohort, threshold: float | None) -> dict:
    predictions = [decide(classes, row, threshold).reason for row in probabilities]
    accepted = sum(reason is not None for reason in predictions)
    correct = sum(
        reason == target for reason, target in zip(predictions, cohort.labels, strict=True)
    )
    reported = cohort.failed - len(predictions)
    return {
        "threshold": threshold,
        "accepted": accepted,
        "coverage": accepted / len(predictions),
        "accepted_accuracy": correct / accepted if accepted else None,
        "reported_count": reported,
        "abstained": len(predictions) - accepted,
        "all_failure_coverage": (accepted + reported) / cohort.failed,
    }


def choose_threshold(model, validation: Cohort) -> tuple[float | None, list[dict]]:
    probabilities = model.predict_proba(validation.features)
    sweep = [
        selective_metrics(model.classes_, probabilities, validation, threshold)
        for threshold in (0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
    ]
    candidates = [
        row for row in sweep if row["coverage"] >= 0.1 and row["accepted_accuracy"] >= 0.7
    ]
    # Maximum coverage at the declared accuracy floor. Ties favor the higher threshold.
    selected = max(candidates, key=lambda row: (row["coverage"], row["threshold"]), default=None)
    return (selected["threshold"] if selected else None), sweep
