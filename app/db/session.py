from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.config import get_settings

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)
engine = None


def configure_database(database_url: str | None = None) -> None:
    global engine

    target_url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if target_url.startswith("sqlite") else {}
    next_engine = create_engine(target_url, future=True, connect_args=connect_args)

    previous_engine = engine
    engine = next_engine
    SessionLocal.configure(bind=engine)

    if previous_engine is not None:
        previous_engine.dispose()


configure_database()


def init_db() -> None:
    import app.db.models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
