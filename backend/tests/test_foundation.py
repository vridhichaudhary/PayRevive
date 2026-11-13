from unittest.mock import MagicMock

import pytest
from app.config import Settings
from app.health import DependencyProbe
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError


class Probe:
    def __init__(self, checks: dict[str, bool]) -> None:
        self.checks = checks

    def check(self) -> dict[str, bool]:
        return self.checks


@pytest.mark.parametrize("dependency", ["postgres", "schema", "redis"])
def test_readiness_fails_closed_but_process_stays_alive(dependency: str) -> None:
    checks = {"postgres": True, "schema": True, "redis": True}
    checks[dependency] = False
    with TestClient(create_app(Settings(), Probe(checks))) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["checks"][dependency] is False


def test_readiness_succeeds_only_when_dependencies_and_schema_are_ready() -> None:
    with TestClient(
        create_app(Settings(), Probe({"postgres": True, "schema": True, "redis": True}))
    ) as client:
        assert client.get("/health/ready").status_code == 200


def test_probe_checks_redis_even_when_database_is_unavailable() -> None:
    engine, redis = MagicMock(), MagicMock()
    engine.connect.side_effect = RuntimeError("secret connection details")
    redis.ping.return_value = True
    assert DependencyProbe(engine, redis).check() == {
        "postgres": False,
        "schema": False,
        "redis": True,
    }


def test_probe_rejects_wrong_schema_and_handles_redis_failure() -> None:
    engine, redis = MagicMock(), MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar.return_value = "wrong_revision"
    redis.ping.side_effect = ConnectionError("secret redis details")
    assert DependencyProbe(engine, redis).check() == {
        "postgres": True,
        "schema": False,
        "redis": False,
    }


@pytest.mark.parametrize("mode", ["live", "sandbox", "production"])
def test_execution_mode_cannot_enable_financial_execution(mode: str) -> None:
    with pytest.raises(ValidationError):
        Settings(execution_mode=mode)


def test_settings_read_environment_and_hide_connection_credentials(monkeypatch) -> None:
    monkeypatch.setenv("PAYREVIVE_DATABASE_URL", "postgresql+psycopg://user:private@db/demo")
    settings = Settings()
    assert "private" not in repr(settings)
    assert "private" not in settings.model_dump_json()
    assert "@db/demo" in settings.database_url.get_secret_value()


@pytest.mark.parametrize("url", ["sqlite:///test.db", "postgresql://localhost/demo"])
def test_database_requires_the_postgres_driver(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=url)


def test_api_storage_failure_is_retryable_without_exposing_details(monkeypatch):
    from uuid import uuid4

    from app.ingestion import routes
    from sqlalchemy.exc import OperationalError

    def fail(*args):
        raise OperationalError("private SQL", {}, Exception("private credentials"))

    monkeypatch.setattr(routes, "create_payment", fail)
    app = create_app(Settings(), Probe({"postgres": True, "schema": True, "redis": True}))
    with TestClient(app) as client:
        app.state.engine = MagicMock()
        response = client.post(
            f"/v1/organizations/{uuid4()}/payments",
            json={"reference": "subscription", "amount_minor": 100},
        )
        assert response.status_code == 503
        assert "same payment or event identity" in response.json()["detail"]
        assert "private" not in response.text
