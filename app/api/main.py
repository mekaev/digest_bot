from fastapi import FastAPI

from app.api.routes.catalog import router as catalog_router
from app.api.routes.health import router as health_router
from app.api.routes.subscriptions import router as subscriptions_router
from app.api.routes.web import router as web_router
from app.api.session_middleware import SignedSessionMiddleware
from app.bootstrap import bootstrap_application
from app.config import get_settings
from app.logging_config import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    bootstrap_application()

    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
    )
    application.add_middleware(
        SignedSessionMiddleware,
        secret_key=settings.web_session_secret.strip() or settings.bot_token.strip(),
        same_site="lax",
    )
    application.include_router(health_router)
    application.include_router(catalog_router)
    application.include_router(subscriptions_router)
    application.include_router(web_router)
    return application


app = create_app()
