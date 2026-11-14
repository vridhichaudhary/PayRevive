from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis import Redis
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, get_settings
from app.dashboard.routes import router as dashboard_router
from app.db import build_engine
from app.features.routes import router as features_router
from app.health import DependencyProbe, ReadinessProbe
from app.ingestion.routes import router
from app.ingestion.service import DomainError
from app.recovery.routes import router as recovery_router


def create_app(settings: Settings | None = None, probe: ReadinessProbe | None = None) -> FastAPI:
    config = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if probe is not None:
            application.state.probe = probe
            yield
            return
        engine = build_engine(config)
        redis = Redis.from_url(
            config.redis_url.get_secret_value(),
            socket_connect_timeout=config.dependency_timeout_seconds,
            socket_timeout=config.dependency_timeout_seconds,
            retry_on_timeout=False,
        )
        application.state.engine = engine
        application.state.probe = DependencyProbe(engine, redis)
        try:
            yield
        finally:
            redis.close()
            engine.dispose()

    application = FastAPI(title="PayRevive", version="0.1.0", lifespan=lifespan)

    application.include_router(router)
    application.include_router(recovery_router)
    application.include_router(features_router)
    application.include_router(dashboard_router)

    @application.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError):
        return JSONResponse(status_code=error.status, content={"detail": error.detail})

    @application.exception_handler(SQLAlchemyError)
    async def storage_error(request: Request, error: SQLAlchemyError):
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Storage unavailable; retry with the same payment or event identity"
            },
        )

    @application.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive", "execution_mode": config.execution_mode}

    @application.get("/health/ready")
    def ready(request: Request) -> JSONResponse:
        checks = request.app.state.probe.check()
        healthy = bool(checks) and all(checks.values())
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ready" if healthy else "not_ready", "checks": checks},
        )

    return application


app = create_app()
