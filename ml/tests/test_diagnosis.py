import numpy as np
import pytest

from ml.data.evaluation import load_truth
from ml.data.loading import load_operational
from simulator.artifacts import export_dataset
from simulator.config import GeneratorConfig, month_start

pytestmark = pytest.mark.analysis


def test_diagnosis_routes_known_unknown_and_low_confidence():
    from ml.diagnosis import model

    assert model.diagnose("expired_method", None, None, None).source == "reported"
    assert model.decide(["risk_block", "unknown"], np.array([0.1, 0.9]), 0.5).reason is None
    assert model.decide(["risk_block", "unknown"], np.array([0.6, 0.4]), 0.7).reason is None
    assert model.decide(["risk_block", "unknown"], np.array([1.0, 0]), None).reason is None
    assert model.decide(["risk_block", "unknown"], np.array([0.6, 0.4]), 0.6).reason == "risk_block"


def test_training_cohort_is_ambiguous_failures_only(tmp_path):
    from ml.diagnosis import experiment, model

    directory = tmp_path / "data"
    config = GeneratorConfig(customers=200)
    export_dataset(config, directory)
    data = load_operational(directory)
    truth = load_truth(data)
    month = month_start(config.start, 1)
    cohort = experiment.cohort(data, truth, month)
    labels = {row.payment_ref: row.true_failure_category for row in truth.labels}
    failed = [
        attempt
        for attempt in data.attempts
        if attempt.status == "failed" and attempt.occurred_at.month == month.month
    ]
    ambiguous = [a for a in failed if a.reason is None or a.reason == "unknown"]
    assert cohort.failed == len(failed)
    assert cohort.labels == [labels[a.payment_ref] for a in ambiguous]
    assert tuple(cohort.features.columns) == model.INPUTS
    assert len(cohort.features) == len(ambiguous)
