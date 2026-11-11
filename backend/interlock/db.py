from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@lru_cache
def engine(url: str):
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("PostgreSQL is required")
    return create_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"options": "-c statement_timeout=15000"},
    )


@contextmanager
def transaction(url: str):
    with Session(engine(url), expire_on_commit=False) as session, session.begin():
        yield session
