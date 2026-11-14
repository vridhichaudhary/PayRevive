"""Event-time daily decline monitoring using operational outcomes only."""

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from math import sqrt
from typing import Literal
from uuid import UUID

from ml.data.loading import OperationalDataset
from simulator.config import month_start


@dataclass(frozen=True)
class Policy:
    lookback_days: int = 14
    min_current: int = 20
    min_baseline: int = 100
    min_increase: float = 0.10
    min_z: float = 3.0
    fixed_rate: float = 0.35

    def __post_init__(self):
        if min(self.lookback_days, self.min_current, self.min_baseline) < 1:
            raise ValueError("Lookback and minimum counts must be positive")
        if not (0 < self.min_increase <= 1 and 0 < self.fixed_rate <= 1 and self.min_z > 0):
            raise ValueError("Effect size, fixed rate and score thresholds must be positive")


@dataclass(frozen=True)
class Window:
    org_id: UUID
    scope: Literal["issuer", "gateway"]
    target: str
    day: date
    attempts: int
    failures: int
    baseline_attempts: int
    baseline_failures: int
    eligible: bool
    rate: float | None
    baseline_rate: float | None
    z: float | None
    rolling_alert: bool
    fixed_alert: bool


def score_windows(data: OperationalDataset, policy: Policy = Policy()) -> list[Window]:
    """Score each UTC bucket at its close; the baseline ends at its opening."""
    totals, failures = Counter(), Counter()
    groups = set()
    for attempt in data.attempts:
        feature = data.features_by_ref[attempt.payment_ref]
        day = attempt.occurred_at.date()
        for scope in ("issuer", "gateway"):
            group = (attempt.org_id, scope, getattr(feature, scope))
            groups.add(group)
            totals[group, day] += 1
            failures[group, day] += attempt.status == "failed"

    result = []
    start = data.config.start
    period = (month_start(start, data.config.months) - start).days
    for org, scope, target in sorted(groups):
        group = (org, scope, target)
        baseline_n = baseline_f = 0
        for offset in range(period):
            day = start + timedelta(days=offset)
            key = group, day
            n, f = totals[key], failures[key]
            rate = f / n if n else None
            baseline_rate = baseline_f / baseline_n if baseline_n else None
            eligible = n >= policy.min_current and baseline_n >= policy.min_baseline
            z = None
            rolling_alert = False
            if eligible:
                assert rate is not None and baseline_rate is not None
                pooled = (f + baseline_f) / (n + baseline_n)
                variance = pooled * (1 - pooled) * (1 / n + 1 / baseline_n)
                if variance:
                    increase = rate - baseline_rate
                    z = increase / sqrt(variance)
                    rolling_alert = increase >= policy.min_increase and z >= policy.min_z
            result.append(
                Window(
                    org,
                    scope,
                    target,
                    day,
                    n,
                    f,
                    baseline_n,
                    baseline_f,
                    eligible,
                    rate,
                    baseline_rate,
                    z,
                    rolling_alert,
                    eligible and rate is not None and rate >= policy.fixed_rate,
                )
            )
            baseline_n += n
            baseline_f += f
            expired = group, day - timedelta(days=policy.lookback_days)
            baseline_n -= totals[expired]
            baseline_f -= failures[expired]
    return result
