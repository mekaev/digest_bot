from app.db.session import SessionLocal, init_db
from app.services.catalog_service import CatalogService


def bootstrap_application() -> None:
    init_db()
    with SessionLocal() as session:
        CatalogService(session).seed_catalog()
