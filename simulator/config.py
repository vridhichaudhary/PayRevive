from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

GENERATOR_VERSION = "1.0.0"


def month_start(start: date, offset: int) -> date:
    index = start.year * 12 + start.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


class GeneratorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    seed: Annotated[StrictInt, Field(ge=0, le=2**32 - 1)] = 42
    customers: Annotated[StrictInt, Field(ge=30, le=50000)] = 6000
    months: Annotated[StrictInt, Field(ge=2, le=12)] = 3
    start: date = date(2026, 1, 1)
    org_id: UUID = UUID("00000000-0000-0000-0000-000000000001")
    incident_strength: Annotated[float, Field(ge=0, le=2, allow_inf_nan=False)] = 1.0
    reason_disclosure_rate: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 0.65

    @model_validator(mode="after")
    def historical_months(self):
        if self.start.day != 1:
            raise ValueError("Start must be the first day of a calendar month")
        end = datetime.combine(month_start(self.start, self.months), datetime.min.time(), UTC)
        if end > datetime.now(UTC):
            raise ValueError("The complete generation period must be in the past")
        return self
