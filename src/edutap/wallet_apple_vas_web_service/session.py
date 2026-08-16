"""The database engine and the per-request session.

No DDL. The schema is created and migrated by the migration container, which is
the only thing that holds the rights to do it and the only thing that records
what it did. The previous implementation issued the schema-creation DDL inside
the request dependency, so every request raced every other one and the service
silently owned a schema it is only a user of.
"""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import URL, Engine
from sqlmodel import Session, create_engine

from .config import AppleWalletWebServiceSettings


@lru_cache(maxsize=8)
def get_engine(url: URL) -> Engine:
    """Return the process-wide engine for one URL.

    Cached by URL, not rebuilt per request: an engine owns a connection pool,
    and one per request is a new pool per request. A `sqlalchemy.URL` object
    is hashable, the same as the string this took before, so the cache still
    works -- see `DatabaseSettings.url()` for why it is a `URL` and not an
    f-string DSN: an f-string is the plaintext password itself, and
    `create_engine` binds its `url` argument as a local of its own frame, so
    a construction failure -- a typo'd dialect, an unreachable host -- would
    otherwise put it in clear text on the raised exception's traceback.

    No `echo`. It writes every statement including its bound parameters, which
    here are push tokens and pass serial numbers.
    """
    return create_engine(url, pool_pre_ping=True)


def get_session() -> Generator[Session]:
    """Yield one session per request; used as a FastAPI dependency."""
    engine = get_engine(AppleWalletWebServiceSettings().db.url())
    with Session(engine) as session:
        yield session
