"""Run inside the API container after Compose becomes healthy."""

from uuid import uuid4

import httpx
from app.config import get_settings
from app.db import SCHEMA_REVISION, build_engine
from app.worker import celery_app
from domain_smoke import verify_ingestion
from sqlalchemy import text


def main() -> None:
    with httpx.Client(base_url="http://localhost:8000", timeout=10) as client:
        response = client.get("/health/ready")
        response.raise_for_status()
        assert all(response.json()["checks"].values())
    engine = build_engine(get_settings())
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == (SCHEMA_REVISION)
    finally:
        engine.dispose()
    token = str(uuid4())
    result = celery_app.send_task("payrevive.foundation_probe", args=[token])
    try:
        assert result.get(timeout=30) == {"token": token, "execution_mode": "simulation"}
    finally:
        result.forget()
    print("PASS: API readiness, migrated PostgreSQL, Redis, and real Celery worker execution")
    verify_ingestion()


if __name__ == "__main__":
    main()
