"""Where this package's tables live, for the schema tool and for Alembic.

`edutap.db_definitions` reads `definition` through an entry point to check this
package against the others that share the database. It is a development
dependency, never a runtime one — the service never calls it, only the separate
CLI that renders and applies DDL. The import is therefore guarded, so a
deployment without the tool installed still imports this module cleanly.
"""

try:
    from edutap.db_definitions import SchemaDefinition
except ModuleNotFoundError:  # pragma: no cover - the service does not need the tool
    SchemaDefinition = None  # type: ignore[assignment]

from . import db_models  # noqa: F401  importing registers the tables on the metadata
from .base import SCHEMA, metadata

PACKAGE_NAME = "edutap.wallet_apple_vas_web_service"

VERSION_TABLE = "alembic_version_wallet_apple_vas_web_service"
"""This package's migration history, named so it cannot be mistaken for another's.

Several packages share one database, which means several histories. In a shared
`alembic_version` the second package's first migration would read the first
package's revision as its own state and skip its own baseline.
"""

OWNED_SCHEMAS: frozenset[str] = frozenset({SCHEMA})

definition = (
    SchemaDefinition(
        name=PACKAGE_NAME,
        metadata=metadata,
        version_table=VERSION_TABLE,
        version_table_schema=SCHEMA,
    )
    if SchemaDefinition is not None
    else None
)
