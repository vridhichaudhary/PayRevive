from dataclasses import replace
from datetime import timedelta

from ml.data.loading import load_operational
from ml.detection.detector import Policy, score_windows
from ml.detection.evaluation import evaluate
from simulator.artifacts import export_dataset
from simulator.config import GeneratorConfig


def test_baseline_is_strictly_prior_and_truth_is_optional(tmp_path):
    dataset = tmp_path / "dataset"
    export_dataset(GeneratorConfig(customers=90), dataset)
    (dataset / "truth/incidents.jsonl").unlink()
    data = load_operational(dataset)
    policy = Policy(lookback_days=4, min_current=1, min_baseline=1)
    windows = score_windows(data, policy)
    for window in windows:
        prior = [
            previous
            for previous in windows
            if (window.org_id, window.scope, window.target)
            == (previous.org_id, previous.scope, previous.target)
            and timedelta(days=1) <= window.day - previous.day <= timedelta(days=4)
        ]
        assert window.baseline_attempts == sum(w.attempts for w in prior)
        assert window.baseline_failures == sum(w.failures for w in prior)
        if not window.attempts:
            assert window.rate is None
            assert not window.rolling_alert and not window.fixed_alert

    cutoff = data.config.start + timedelta(days=18)
    changed = replace(
        data,
        attempts=tuple(
            a.model_copy(update={"status": "failed"}) if a.occurred_at.date() >= cutoff else a
            for a in data.attempts
        ),
    )
    assert [w for w in score_windows(changed, policy) if w.day < cutoff] == [
        w for w in windows if w.day < cutoff
    ]


def test_evaluation_requires_target_and_time_match(tmp_path):
    dataset = tmp_path / "dataset"
    export_dataset(GeneratorConfig(customers=30), dataset)
    data = load_operational(dataset)
    from ml.data.evaluation import load_truth

    truth = load_truth(data)
    incident = truth.incidents[0]
    day = incident.starts_at.date()
    windows = score_windows(data)
    matching = next(w for w in windows if w.target == incident.target and w.day == day)
    alert = replace(matching, eligible=True, rolling_alert=True)
    metrics = evaluate(
        [alert, replace(alert, target="different"), replace(alert, day=incident.ends_at.date())],
        (incident,),
    )["methods"]["rolling"]
    assert metrics["true_alert_windows"] == 1
    assert metrics["false_alert_windows"] == 2
    assert metrics["incidents_detected"] == 1
    assert metrics["incidents"][0]["delay_hours"] == 24
    assert evaluate([alert], ())["methods"]["rolling"]["incident_recall"] is None
