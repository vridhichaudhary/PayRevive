from celery import Celery

from app.config import get_settings

settings = get_settings()
celery_app = Celery(
    "payrevive",
    broker=settings.redis_url.get_secret_value(),
    backend=settings.result_backend_url.get_secret_value(),
    include=["app.tasks", "app.recovery.tasks"],
)
celery_app.conf.update(
    beat_schedule={"recovery-sweep": {"task": "payrevive.recovery_sweep", "schedule": 5.0}},
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    enable_utc=True,
    timezone="UTC",
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=settings.dependency_timeout_seconds,
    broker_transport_options={"socket_connect_timeout": 2, "socket_timeout": 2},
    worker_prefetch_multiplier=1,
    result_expires=300,
    task_soft_time_limit=20,
    task_time_limit=30,
)
