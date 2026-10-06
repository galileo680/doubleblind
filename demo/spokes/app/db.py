import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

DATABASE_URL_ENV = "SPOKES_DATABASE_URL"
DEFAULT_DATABASE_URL = "sqlite:///./spokes.db"
IN_MEMORY_URLS = frozenset({"sqlite://", "sqlite:///:memory:"})


class Base(DeclarativeBase):
    pass


def build_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False}
    if url in IN_MEMORY_URLS:
        return create_engine(url, connect_args=connect_args, poolclass=StaticPool)
    return create_engine(url, connect_args=connect_args)


engine = build_engine(os.environ.get(DATABASE_URL_ENV, DEFAULT_DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def reset_db() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
