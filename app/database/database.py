import logging
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings

logger = logging.getLogger(__name__)

_IN_MEMORY_URLS = {"sqlite://", "sqlite:///:memory:"}


class Base(DeclarativeBase):
    pass


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _enable_wal(dbapi_connection, _connection_record) -> None:
    # WAL lets readers proceed while a booking transaction is writing.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def make_engine(url: str) -> Engine:
    """Create an engine. In-memory SQLite uses a single shared connection (used by the tests)."""
    kwargs: dict = {}
    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in _IN_MEMORY_URLS:
            kwargs["poolclass"] = StaticPool

    engine = create_engine(url, **kwargs)
    if is_sqlite:
        event.listen(engine, "connect", _configure_sqlite)
        if url not in _IN_MEMORY_URLS:
            event.listen(engine, "connect", _enable_wal)
    return engine


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine)


def init_db(target: Engine | None = None) -> None:
    """Create all tables (and the SQLite overlap-guard triggers) if they do not exist."""
    import app.models  # noqa: F401  (registers every model on Base.metadata)

    Base.metadata.create_all(target or engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    with SessionLocal() as session:
        yield session
