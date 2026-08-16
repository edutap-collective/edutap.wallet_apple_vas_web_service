"""Package-local metadata and declarative base.

The naming convention is COPIED from `edutap.db_definitions` rather than
imported: importing would give this deployed service a runtime dependency on a
tool that is never deployed. `edutap-dbdef check` verifies that every package
uses the same convention, which is what keeps the copies honest.

The metadata is package-local because `SQLModel.metadata` is a process-wide
singleton. Several packages share this database, and a generator that cannot
tell them apart cannot order, split or diff them.
"""

from sqlalchemy import MetaData
from sqlmodel import SQLModel

NAMING_CONVENTION: dict[str, str] = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}

SCHEMA = "wallet_apple_vas"
"""The one schema this package owns.

Written down once here rather than repeated per table, so that
`dbdef.OWNED_SCHEMAS` and the tables cannot drift apart. Not `public`: that
schema is the estate's cross-package contract.
"""

metadata = MetaData(naming_convention=NAMING_CONVENTION, schema=SCHEMA)


class Base(SQLModel):
    """Declarative base binding this package's tables to its own metadata."""

    metadata = metadata
