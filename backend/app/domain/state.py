from datetime import datetime
from typing import Literal

Status = Literal["pending", "failed", "succeeded"]
RANK = {"pending": 0, "failed": 1, "succeeded": 2}


def next_attempt_state(
    current: Status, state_at: datetime, incoming: Status, occurred_at: datetime
) -> tuple[Status, datetime]:
    """Success is absorbing. Otherwise latest observation wins; ties use stable precedence."""
    if current == "succeeded":
        return current, state_at
    if incoming == "succeeded" or (occurred_at, RANK[incoming]) > (state_at, RANK[current]):
        return incoming, occurred_at
    return current, state_at
