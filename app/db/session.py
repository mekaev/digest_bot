from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
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
    _sync_schema()


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _sync_schema() -> None:
    if engine is None:
        return

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    statements: list[str] = []

    if "channels" in table_names:
        channel_columns = {column["name"] for column in inspector.get_columns("channels")}
        if "is_user_added" not in channel_columns:
            statements.append(
                "ALTER TABLE channels ADD COLUMN is_user_added BOOLEAN NOT NULL DEFAULT FALSE"
            )
        if "added_by_user_id" not in channel_columns:
            statements.append("ALTER TABLE channels ADD COLUMN added_by_user_id INTEGER")

    if "digest_schedules" in table_names:
        schedule_columns = {column["name"] for column in inspector.get_columns("digest_schedules")}
        if "window_days" not in schedule_columns:
            statements.append(
                "ALTER TABLE digest_schedules ADD COLUMN window_days INTEGER NOT NULL DEFAULT 7"
            )

    if "posts" in table_names:
        post_columns = {column["name"] for column in inspector.get_columns("posts")}
        for column_name in (
            "views_count",
            "reactions_count",
            "forwards_count",
            "comments_count",
        ):
            if column_name not in post_columns:
                statements.append(
                    f"ALTER TABLE posts ADD COLUMN {column_name} INTEGER NOT NULL DEFAULT 0"
                )

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
