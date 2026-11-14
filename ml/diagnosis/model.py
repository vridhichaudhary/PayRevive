"""Fit and apply the offline failure diagnosis models."""

from dataclasses import dataclass
from typing import get_args

import numpy as np
import pandas as pd
from app.features.contracts import MODEL_INPUT_FIELDS, FeatureValues
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from simulator.models import Reason

CLASSES = tuple(sorted(get_args(Reason)))
CATEGORIES = ("segment", "issuer", "network", "gateway")
INPUTS = tuple(
    f for f in MODEL_INPUT_FIELDS if f not in ("prior_payment_count", "prior_success_rate")
)
NUMERIC = tuple(f for f in INPUTS if f not in CATEGORIES)


def feature_frame(rows: list[FeatureValues]) -> pd.DataFrame:
    return pd.DataFrame([row.model_inputs() for row in rows], columns=list(INPUTS))


def fit_models(features: pd.DataFrame, labels: list[str], seed: int) -> dict[str, Pipeline]:
    if len(set(labels)) < 2:
        raise ValueError("Diagnosis training requires at least two observed failure classes")
    models = {}
    for name, estimator in (
        ("majority", DummyClassifier(strategy="prior")),
        ("logistic", LogisticRegression(max_iter=1500)),
        (
            "forest",
            RandomForestClassifier(
                n_estimators=160,
                min_samples_leaf=8,
                max_features="sqrt",
                random_state=seed,
            ),
        ),
    ):
        preprocess = ColumnTransformer(
            [
                (
                    "category",
                    OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                    list(CATEGORIES),
                ),
                (
                    "numeric",
                    Pipeline(
                        [("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]
                    ),
                    list(NUMERIC),
                ),
            ]
        )
        models[name] = Pipeline([("inputs", preprocess), ("classifier", estimator)]).fit(
            features, labels
        )
    return models


@dataclass(frozen=True)
class Diagnosis:
    reason: str | None
    source: str
    confidence: float | None


def diagnose(
    reported: Reason | None,
    features: FeatureValues,
    model: Pipeline,
    threshold: float | None,
) -> Diagnosis:
    """Diagnose a completed failure. This result carries no execution permission."""
    if reported in CLASSES and reported != "unknown":
        return Diagnosis(reported, "reported", None)
    return decide(model.classes_, model.predict_proba(feature_frame([features]))[0], threshold)


def decide(classes, probabilities, threshold: float | None) -> Diagnosis:
    if threshold is not None and not 0 <= threshold <= 1:
        raise ValueError("Confidence threshold must be between zero and one")
    index = int(np.argmax(probabilities))
    confidence = float(probabilities[index])
    reason = str(classes[index])
    if reason == "unknown" or threshold is None or confidence < threshold:
        return Diagnosis(None, "abstained", confidence)
    return Diagnosis(reason, "model", confidence)
