"""Alembic environment for this package, in a database it shares with others.

Everything schema-related is read from the package's own `dbdef` module rather
than repeated here: which schemas it owns, where its migration history lives,
and how autogenerate is kept off other packages' tables. A second copy of any
of them is a second thing to keep in sync.

`dbdef` is imported, `edutap.db_definitions` is not. The tool is a development
dependency and is absent where these migrations actually run; `dbdef` guards
its own import of it and exposes the same facts as plain constants.

This file follows `edutap.wallet_apple_vas_account_binding`, which solved the
same problem first. The reasoning is repeated rather than referenced, because a
reader of this package will not have that one open.
"""

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from logging.config import fileConfig
from typing import Any

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import (
    BLANK_SCHEMA,
    Connection,
    MetaData,
    engine_from_config,
    inspect,
    pool,
    text,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.sql.schema import RETAIN_SCHEMA

from edutap.wallet_apple_vas_web_service.base import metadata
from edutap.wallet_apple_vas_web_service.config import DatabaseSettings
from edutap.wallet_apple_vas_web_service.dbdef import (
    OWNED_SCHEMAS,
    VERSION_TABLE,
    VERSION_TABLE_SCHEMA,
    include_name_for,
)

config = context.config

if config.config_file_name is not None:
    # `disable_existing_loggers=False`, or the first `command.upgrade()` in a
    # process silences every logger configured before it -- which in a pytest
    # session takes the rest of the suite's logging with it.
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def database_url() -> str:
    """Return the database to migrate.

    An explicit `sqlalchemy.url` wins, which is how the test suite points
    Alembic at its container. Otherwise the package's own `DatabaseSettings`
    answers, so `alembic upgrade head` in a deployment reads the same
    `EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_DB_*` environment (and the same
    `_FILE` secrets convention) that the service reads.

    `DatabaseSettings` and not the full `AppleWalletWebServiceSettings`:
    migrating needs a database, not an issuer secret and a producer URL.

    Every `DatabaseSettings` field has a default, and the default host and name
    are `None`. Left unchecked that yields the URL
    `postgresql+psycopg2://None:None@None/None`, which does not fail fast:
    `psycopg2-binary` is installed, so there is no import error, and libpq
    blocks in its resolver. A migration step that hangs is worse than one that
    refuses.

    `render_as_string(hide_password=False)`, because `DatabaseSettings.url()`
    returns a `sqlalchemy.URL` whose default rendering masks the password --
    that masking is the point of the type (see `config.py`), and it is exactly
    what must not reach the connection attempt.
    """
    configured = config.get_main_option("sqlalchemy.url", None)
    if configured:
        return configured
    settings = DatabaseSettings()
    if not settings.host or not settings.name:
        raise RuntimeError(
            "No database configured for the migrations. Set "
            "EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_DB_HOST and _DB_NAME (and the "
            "credentials), or give alembic.ini an explicit sqlalchemy.url."
        )
    return settings.url().render_as_string(hide_password=False)


def create_owned_schemas(connection: Connection) -> None:
    """Create this package's schemas, before Alembic writes anything.

    Measured against Alembic 1.19.1: `MigrationContext.run_migrations` calls
    `_ensure_version_table()` *before* it runs the first revision. On an empty
    database that means `CREATE TABLE
    wallet_apple_vas.alembic_version_wallet_apple_vas_web_service` is the very
    first statement -- so a `CREATE SCHEMA` inside the baseline migration would
    come too late, and the upgrade fails with `InvalidSchemaName` before any
    migration code runs at all. The schema has to come from here.

    Called inside Alembic's own transaction, so on PostgreSQL a failing first
    migration takes the schema down with it rather than leaving an empty one
    behind.

    Asks first, and `CREATE SCHEMA IF NOT EXISTS` is not enough to make that
    unnecessary: PostgreSQL checks `CREATE` **on the database** before it
    checks whether the schema exists, so against a database provisioned the way
    the estate intends -- a superuser runs `CREATE SCHEMA wallet_apple_vas
    AUTHORIZATION <ddl role>`, and the DDL role owns the schema without holding
    `CREATE ON DATABASE` -- the statement fails with `InsufficientPrivilege:
    permission denied for database`. That would make the deploy step die on its
    first statement against a database that is already correct, and would make
    even `alembic current` unusable for the service role, since this function
    runs on every online Alembic command.

    So the `CREATE` is only reached where the schema really is missing, which
    is the fresh developer database and the test database -- and there the role
    does have `CREATE ON DATABASE`.
    """
    existing = set(inspect(connection).get_schema_names())
    for schema in sorted(OWNED_SCHEMAS - existing):
        # The schema names come from this package's own metadata, not from input.
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))


@contextmanager
def reflection_search_path(connection: Connection) -> Iterator[None]:
    """Pin `search_path` to the default schema while Alembic reflects.

    This is the difference between a bounded autogenerate and one that emits
    `op.drop_table` for another service's tables, and it is not the same
    problem `include_name` solves.

    PostgreSQL's reflection omits the schema of everything **visible on the
    `search_path`**, not of everything in the default schema. Alembic's rule is
    the narrower one: only the default schema is `None`. The two coincide
    exactly when the path holds nothing but the default schema.

    With `search_path = "$user", wallet_apple_vas, public` and therefore
    `default_schema_name = wallet_apple_vas`, `get_table_names(schema=None)`
    returns another package's `public.alembic_version` alongside this package's
    own tables, unqualified. `include_name` reads them as `wallet_apple_vas`,
    admits them, finds them in no metadata -- and a real
    `alembic revision --autogenerate` writes `op.drop_table("alembic_version")`
    with no `schema=`, so applying it resolves against the same `search_path`
    and destroys another package's migration history.

    Pinning is a smaller intervention than it looks: every schema Alembic
    reflects is named explicitly, so this changes only whether the names come
    back qualified.

    `SET` is transactional in PostgreSQL, so an aborted transaction rolls it
    back on its own; a restore that fails because the block left the
    transaction in `InFailedSqlTransaction` is suppressed rather than allowed
    to mask the error that put it there.
    """
    previous = connection.exec_driver_sql("SHOW search_path").scalar()
    _set_search_path(connection, connection.dialect.default_schema_name or "")
    try:
        yield
    finally:
        with suppress(DBAPIError):
            _set_search_path(connection, previous or "")


def _set_search_path(connection: Connection, value: str) -> None:
    """Set `search_path` through `set_config`, so the value stays a parameter.

    `SET search_path TO ...` takes an identifier list, which would have to be
    quoted by hand and is fed here with a value read back out of the database.
    `set_config` takes it as a string, so the driver binds it.
    """
    connection.execute(text("SELECT set_config('search_path', :value, false)"), {"value": value})


def comparison_metadata(connection: Connection) -> MetaData:
    """Return the package metadata with the connection's default schema folded away.

    Alembic represents the connection's default schema as `None`: it reflects
    it under that key, hands `include_name` `None` for it, and keys its
    `conn_table_names` by it. The declared metadata says `wallet_apple_vas`.
    Where the two are the same schema -- a DDL role whose `search_path` starts
    with this package's own schema, which is how Ansible provisions one -- the
    declaration has to be folded to `None` for the comparison, or every table
    is compared against nothing.

    `reflection_search_path` already makes the *reflected* side obey Alembic's
    rule; this is the other side of the same equation, and neither alone is
    enough.

    Measured against Alembic 1.19.1 and PostgreSQL 18.4, with the fold absent:
    under `search_path = wallet_apple_vas` an autogenerate run against a
    database that `upgrade head` had just built proposed
    `op.drop_constraint`/`op.create_foreign_key` for **both** of
    `registration`'s foreign keys, on every run and for ever. Under the role
    default (`"$user", public`) it proposed nothing, which is why the fold is
    easy to omit and hard to notice.

    Folding the table is not enough on its own -- the foreign key's target
    carries a schema too, and folding one without the other raises
    `NoReferencedTableError` for a key that points inside the folded schema,
    which is what both of ours do. That is why `referred_schema_fn` is passed.

    A column *type* can carry a schema as well (an `Enum`, a `DOMAIN`), and
    `edutap.db_definitions.compare` folds those too. This package has none:
    every column is a `String`, a `BigInteger` or a `DateTime`. If one is ever
    added, this function has to grow the third fold -- see
    `edutap.db_definitions.compare._folded_type` for what it takes.

    Folding is not the same as dropping the declaration. The tables keep
    declaring their schema explicitly, which is what keeps them out of
    `search_path`'s hands at runtime; this copy exists solely so that the
    comparison sees the shape Alembic produces.
    """
    default_schema = connection.dialect.default_schema_name
    if default_schema not in OWNED_SCHEMAS:
        return metadata

    def referred_schema_fn(table, to_schema, constraint, referred_schema):
        # BLANK_SCHEMA clears it; returning None would mean "leave unchanged".
        return BLANK_SCHEMA if referred_schema == default_schema else RETAIN_SCHEMA

    folded = MetaData(naming_convention=metadata.naming_convention)
    for table in metadata.tables.values():
        # `None`, not `BLANK_SCHEMA`: this argument is concatenated into the
        # table key, and `None` means "take the target MetaData's schema",
        # which for `folded` is no schema at all.
        table.to_metadata(
            folded,
            schema=None if table.schema == default_schema else table.schema,
            referred_schema_fn=referred_schema_fn,
        )
    return folded


def render_item(type_: str, obj: Any, autogen_context: AutogenContext) -> bool:
    """Declare the import a rendered SQLModel column type needs.

    SQLModel's `AutoString` renders as `sqlmodel.sql.sqltypes.AutoString()`,
    and Alembic emits no import for it -- an autogenerated revision then fails
    ruff with `F821 Undefined name 'sqlmodel'`, and would fail at import time
    rather than at migration time.

    No column in this package renders that way today: every one of them is
    declared with an explicit `sa_column`, so `0001_initial` came out with
    `sa.String`, `sa.BigInteger` and `sa.DateTime` and no SQLModel type at all.
    The hook is here because the *next* field to be added need only be written
    as a plain `Field(...)` for `AutoString` to appear, and the failure is a
    revision that cannot be imported.

    Registering the import and returning `False` keeps Alembic's own rendering,
    which is the point: the revision then says exactly which type the model
    declared, rather than a hand-picked `sa.String()` that happens to produce
    the same DDL today.
    """
    if type_ == "type" and obj.__class__.__module__.startswith("sqlmodel"):
        autogen_context.imports.add("import sqlmodel.sql.sqltypes")
    return False


def run_migrations_offline() -> None:
    """Emit the migrations as SQL instead of running them.

    For a deployment that wants the DDL reviewed, or applied by hand under a
    role this process does not have. There is no connection, so there is
    nothing to reflect and no autogenerate: `include_name` is a reflection
    filter and would have nothing to filter here.
    """
    context.configure(
        url=database_url(),
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
        version_table_schema=VERSION_TABLE_SCHEMA,
    )

    with context.begin_transaction():
        for schema in sorted(OWNED_SCHEMAS):
            context.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        context.run_migrations()


def run_migrations_online() -> None:
    """Run the migrations against a live database."""
    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=comparison_metadata(connection),
            include_schemas=True,
            include_name=include_name_for(connection.dialect.default_schema_name),
            render_item=render_item,
            compare_type=True,
            version_table=VERSION_TABLE,
            version_table_schema=VERSION_TABLE_SCHEMA,
        )
        with context.begin_transaction():
            create_owned_schemas(connection)
            # The pin covers autogenerate, which is what needs it, and it
            # covers an upgrade too. That is deliberate rather than incidental:
            # every object these migrations touch is schema-qualified by
            # construction, so a migration that resolves through `search_path`
            # is a bug either way, and a fixed path is one fewer thing that
            # differs between the developer's database and production.
            with reflection_search_path(connection):
                context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
