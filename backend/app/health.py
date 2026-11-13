import logging
from typing import Protocol

from redis import Redis
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import SCHEMA_REVISION

logger = logging.getLogger(__name__)


class ReadinessProbe(Protocol):
    def check(self) -> dict[str, bool]: ...


class DependencyProbe:
    def __init__(self, engine: Engine, redis: Redis) -> None:
        self.engine = engine
        self.redis = redis

    def check(self) -> dict[str, bool]:
        checks = {"postgres": False, "schema": False, "redis": False}
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                checks["postgres"] = True
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
                marker = connection.execute(
                    text("SELECT value FROM application_metadata WHERE key = 'execution_mode'")
                ).scalar()
                checks["schema"] = revision == SCHEMA_REVISION and marker == "simulation"
        except Exception:
            # Never log exception details: connection errors can contain credentials.
            logger.warning("readiness_database_check_failed")
        try:
            checks["redis"] = bool(self.redis.ping())
        except Exception:
            logger.warning("readiness_redis_check_failed")
        return checks
