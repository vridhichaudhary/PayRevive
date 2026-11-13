import os
from uuid import uuid4

import pytest
from app.config import Settings
from app.db import build_engine
from app.domain.tables import organizations


@pytest.fixture
def engine():
    if os.getenv("PAYREVIVE_INTEGRATION_TESTS") != "1":
        pytest.fail("Run PostgreSQL tests through the Compose test profile")
    engine = build_engine(Settings())
    assert engine.url.database == "payrevive_test", "Never run tests on demo data"
    yield engine
    engine.dispose()


@pytest.fixture
def org(engine):
    org = uuid4()
    with engine.begin() as connection:
        connection.execute(organizations.insert().values(id=org, name="Integration test"))
    return org
