"""Where this package's tables live, for the schema tool and for Alembic.

Two readers, one file. `edutap.db_definitions` reads `definition` through an
entry point to check this package against the others that share the database;
the Alembic `env.py` reads the plain constants and `include_name_for` above it.
Declaring either of them twice is how they drift.

`edutap.db_definitions` is a development dependency, never a runtime one — the
service never calls it, only the separate CLI that renders and applies DDL. The
import is therefore guarded, so a deployment without the tool installed still
imports this module cleanly; `definition` is `None` in that case.

That guard is why `env.py` reads the constants rather than `definition`: the
migrations run in production, where the tool is absent and `definition` is
`None`.
"""

from collections.abc import Callable
from typing import Any

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
"""The schemas this package is responsible for.

One, and `base.SCHEMA` is where it is written down — the same constant the
metadata and every table are built from, so the three cannot disagree.
`test_dbdef.py` pins it against what the metadata actually says, so a table that
later declares a schema of its own does not quietly fall outside the bound
`include_name_for` draws from this set.
"""

VERSION_TABLE_SCHEMA: str = SCHEMA
"""The schema Alembic's history table for this package belongs in.

Alembic's fallback for a missing `version_table_schema` is the connection's
default schema, which is `public`: the one schema the estate reserves for the
cross-package contract, and the last place several packages' histories should
collect.
"""


def include_name_for(default_schema: str | None) -> Callable[[str | None, str, dict], bool]:
    """Return Alembic's `include_name` hook, bounded to this package's schemas.

    Without it, `include_schemas=True` shows autogenerate every table in the
    shared database, and it proposes dropping every one it does not find in
    this package's metadata — `public.person_view`, `binding.*`, and whatever
    else the estate happens to run. Applied unread, such a migration deletes
    other services' data. The bound is therefore the condition for autogenerate
    being usable here at all, not a refinement of it.

    Bounded by *schema*, not by known table name: a table that appears inside
    `wallet_apple_vas` without this package declaring it is drift in something
    this package owns, and autogenerate should say so.

    `default_schema` is the connection's own default schema, read from
    `connection.dialect.default_schema_name` rather than assumed to be
    `"public"`. Alembic passes `None` for whichever schema that is — both as
    the schema's own name and as a table's `parent_names["schema_name"]` — so
    both are normalised through it before the comparison. A role whose
    `search_path` starts with `wallet_apple_vas` therefore has this package's
    own schema reported as `None`, and folding that `None` to a literal
    `"public"` would put the package's own tables outside its own bound:
    autogenerate would see none of them and propose creating all three again.

    **This bound is only bounded if the reflection is pinned.** Normalising
    `None` to the default schema is Alembic's rule; PostgreSQL's is wider — it
    returns everything *visible on the `search_path`* unqualified, which would
    hand this function another package's `public.alembic_version` with
    `schema_name=None`, which it would then read as `wallet_apple_vas` and
    admit. The remedy is not here, it is `env.py::reflection_search_path`. Read
    the two together; neither is sufficient alone.
    """

    def include_name(name: str | None, type_: str, parent_names: dict[str, Any]) -> bool:
        if type_ == "schema":
            return (name or default_schema) in OWNED_SCHEMAS
        if type_ == "table":
            schema = parent_names.get("schema_name") or default_schema
            if schema not in OWNED_SCHEMAS:
                return False
            # Alembic's own history table is not in the metadata, so anything
            # that reflects it proposes dropping it. Alembic drops it from the
            # comparison itself -- but only where `schema_name` equals the
            # configured `version_table_schema`, and it passes `None` for a
            # schema that is the connection's default. Excluding it here does
            # not depend on which schema the connection calls default.
            return not (schema == VERSION_TABLE_SCHEMA and name == VERSION_TABLE)
        return True

    return include_name


definition = (
    SchemaDefinition(
        name=PACKAGE_NAME,
        metadata=metadata,
        version_table=VERSION_TABLE,
        version_table_schema=VERSION_TABLE_SCHEMA,
    )
    if SchemaDefinition is not None
    else None
)
