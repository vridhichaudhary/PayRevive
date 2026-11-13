from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.config import Settings

SCHEMA_REVISION = "0002_domain"


def build_engine(settings: Settings) -> Engine:
    timeout = settings.dependency_timeout_seconds
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_timeout=timeout,
        connect_args={
            "connect_timeout": timeout,
            "options": f"-c statement_timeout={timeout * 1000}",
        },
    )
