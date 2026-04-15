from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.config import get_settings
from app.logging_config import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    application = FastAPI(
        title=settings.app_name,
        version='0.1.0',
    )
    application.include_router(health_router)
    return application


app = create_app()
