from uuid import UUID

from app.config import get_settings
from app.db import build_engine
from app.recovery.dispatch import consume_event, dispatch
from app.recovery.execution import execute, reconcile
from app.worker import celery_app

OPTIONS = dict(
    acks_late=True,
    reject_on_worker_lost=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=5,
)


@celery_app.task(name="payrevive.recovery_event", **OPTIONS)
def event_task(org_id: str, event_id: str):
    engine = build_engine(get_settings())
    try:
        consume_event(engine, UUID(org_id), UUID(event_id))
    finally:
        engine.dispose()


@celery_app.task(name="payrevive.recovery_execute", **OPTIONS)
def execute_task(org_id: str, action_id: str):
    engine = build_engine(get_settings())
    try:
        execute(engine, UUID(org_id), UUID(action_id))
    finally:
        engine.dispose()


@celery_app.task(name="payrevive.recovery_reconcile", **OPTIONS)
def reconcile_task(org_id: str, action_id: str):
    engine = build_engine(get_settings())
    try:
        reconcile(engine, UUID(org_id), UUID(action_id))
    finally:
        engine.dispose()


@celery_app.task(name="payrevive.recovery_sweep", **OPTIONS)
def sweep_task():
    engine = build_engine(get_settings())
    try:
        return dispatch(engine, lambda name, args: celery_app.send_task(name, args=args))
    finally:
        engine.dispose()
