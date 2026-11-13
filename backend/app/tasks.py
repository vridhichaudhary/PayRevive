from sqlalchemy import text

from app.config import get_settings
from app.db import build_engine
from app.worker import celery_app


@celery_app.task(name="payrevive.foundation_probe")
def foundation_probe(token: str) -> dict[str, str]:
    """Harmless integration probe; not a payment execution interface."""
    engine = build_engine(get_settings())
    try:
        with engine.connect() as connection:
            mode = connection.execute(
                text("SELECT value FROM application_metadata WHERE key = 'execution_mode'")
            ).scalar_one()
        if mode != "simulation":
            raise RuntimeError("Unexpected execution mode")
        return {"token": token, "execution_mode": mode}
    finally:
        engine.dispose()
