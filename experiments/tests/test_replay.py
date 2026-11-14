from dataclasses import replace
from datetime import UTC, datetime, timedelta

from experiments.replay import Case, ExperimentConfig, build_cases, compare, replay
from experiments.report import summarize

NOW = datetime(2026, 1, 1, tzinfo=UTC)
CONFIG = ExperimentConfig(runs=2, customers=30, bootstrap_draws=100)


def test_money_attribution_accounts_for_background_settlement_and_cost():
    case = Case(
        50000,
        NOW,
        "insufficient_funds",
        True,
        True,
        NOW + timedelta(hours=72),
        NOW + timedelta(hours=3),
    )
    results = compare([case, replace(case, background_at=None)], CONFIG)
    rule = results["reason_based"]
    assert results["control"]["gross_minor"] == 50000
    assert rule["retry_recovered_minor"] == rule["gross_minor"] == 100000
    assert rule["retry_recovered_also_in_control_minor"] == 50000
    assert rule["incremental_minor"] == 50000
    assert rule["cost_minor"] == 50
    assert rule["net_incremental_minor"] == 49950
    assert rule["retried_without_incremental_settlement"] == 1


def test_eligibility_and_cost_apply_to_both_policies():
    case = Case(200000, NOW, "gateway_error", True, True, None, None)
    for policy in ("fixed", "reason_based"):
        assert replay(case, policy, CONFIG).retries == 2
        for blocked in (
            replace(case, approved=False),
            replace(case, consent=False),
            replace(case, reason="risk_block"),
            replace(case, reason=None),
        ):
            assert replay(blocked, policy, CONFIG).retries == 0
        background = replace(case, background_at=NOW + timedelta(hours=2))
        result = replay(background, policy, CONFIG)
        assert result.settled and result.retries == 0


def test_exogenous_outcomes_are_seeded_and_paired():
    dataset_id, cases = build_cases(71, CONFIG)
    assert (dataset_id, cases) == build_cases(71, CONFIG)
    assert dataset_id != build_cases(72, CONFIG)[0]
    totals = compare(cases, CONFIG)
    assert totals == compare(list(reversed(cases)), CONFIG)
    assert len({row["at_risk_minor"] for row in totals.values()}) == 1
    rows = [{"policies": totals}] * 2
    summary = summarize(rows, CONFIG)
    assert summary == summarize(rows, CONFIG)
    for row in summary:
        assert row["ci95_low_paise"] == row["mean_difference_paise_per_cohort"]
        assert row["ci95_high_paise"] == row["mean_difference_paise_per_cohort"]


def test_horizon_and_negative_net_when_retries_never_settle():
    case = Case(50000, NOW, "insufficient_funds", True, True, None, None)
    short = CONFIG.model_copy(update={"horizon_hours": 23})
    assert replay(case, "reason_based", short).stop == "horizon"
    assert compare([case], CONFIG)["reason_based"]["net_incremental_minor"] == -50
