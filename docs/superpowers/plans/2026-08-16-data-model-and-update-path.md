# Data Model and Update Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace this service's data model with the three tables Apple's storage model describes, derive the authentication token per pass, and answer all four endpoints from the new schema without holding pass content or person data.

**Architecture:** A schema of its own (`wallet_apple_vas`) declared through an entry point in `edutap.db_definitions` and migrated by the migration container. Registration bookkeeping only — pass content is fetched from one configured producer at delivery time. The token is derived by HMAC from an issuer secret rather than stored, so a registration for a pass this service has never seen still authenticates.

**Tech Stack:** Python 3.13+, FastAPI, SQLModel over SQLAlchemy 2.0, PostgreSQL, pydantic-settings, pytest.

**Spec:** [`docs/superpowers/specs/2026-08-16-data-model-and-update-path-design.md`](../specs/2026-08-16-data-model-and-update-path-design.md)

## Scope

This plan covers the web service: schema, token, and the four endpoints Apple
calls. **It does not cover the notifier** — the Kafka consumer that bumps
`last_update_tag` and sends APNs pushes. That is section 6 of the spec and gets
its own plan, because APNs is a separate work package and the event contract
still has an open point (`passTypeIdentifier` is absent from
`public.pass_state`).

Consequence to be aware of while implementing: after this plan, nothing
advances `last_update_tag` except the creation of a pass row. Registration,
listing and delivery all work; automatic updates arrive with the notifier.

## Global Constraints

- **Python floor `>=3.13`**, as `pyproject.toml` already declares.
- **Exact dependency pins.** This is a service, not a library. A new dependency
  is a reviewed change with an `==` pin.
- **Schema is `wallet_apple_vas`.** Never `public` — that schema is the
  cross-package contract.
- **No `create_all` in the request path.** DDL belongs to the migration
  container.
- **Package-local `MetaData`.** `SQLModel.metadata` is a process-wide
  singleton; several packages share this database.
- **Naming convention copied, not imported** from `edutap.db_definitions` — a
  deployed service must not depend on a tool that is never deployed.
- **Constant-time comparison for every secret**, and no early exit that leaks
  which secret matched.
- **Never log or return a push token, an authentication token or a secret.**
- **English.** The repository is public at `edutap-collective`.
- `make lint` must pass (blocking). `make typecheck` reports known defects and
  is non-blocking; do not add ignore comments to silence it.
- Unit tests run under `make test-local`. Tests needing PostgreSQL are marked
  `integration`, run under `make test-integration`, and read the DSN from
  `WALLET_APPLE_VAS_WEB_SERVICE_TEST_DSN`.

## File Structure

| File | Responsibility |
|---|---|
| `src/…/base.py` | **new** — package-local `MetaData`, `SCHEMA`, declarative `Base` |
| `src/…/db_models.py` | **rewritten** — `Device`, `PassRecord`, `Registration`; `ApplePassData` withdrawn |
| `src/…/dbdef.py` | **new** — the `edutap.db_definitions` entry point |
| `src/…/session.py` | **new** — engine and session dependency, no DDL |
| `src/…/tokens.py` | **new** — token derivation and verification |
| `src/…/producer.py` | **new** — fetching a built `.pkpass` from the configured producer |
| `src/…/config.py` | modified — secrets, producer URL template |
| `src/…/http_models.py` | modified — `SerialNumbers` spelling |
| `src/…/service.py` | rewritten — the four endpoints |

---

### Task 1: The schema

**Files:**
- Create: `src/edutap/wallet_apple_vas_web_service/base.py`
- Create: `src/edutap/wallet_apple_vas_web_service/dbdef.py`
- Modify: `src/edutap/wallet_apple_vas_web_service/db_models.py` (full rewrite)
- Modify: `pyproject.toml` (entry point)
- Test: `tests/test_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `base.SCHEMA: str`, `base.metadata: MetaData`, `base.Base`;
  `db_models.Device`, `db_models.PassRecord`, `db_models.Registration`,
  `db_models.UPDATE_TAG_SEQUENCE`; `dbdef.definition`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schema.py
"""The schema this service owns, and its isolation from the shared database."""

import sqlalchemy as sa
from sqlmodel import SQLModel

from edutap.wallet_apple_vas_web_service import db_models
from edutap.wallet_apple_vas_web_service.base import SCHEMA, metadata


def test_metadata_holds_exactly_the_three_tables():
    assert sorted(metadata.tables) == [
        f"{SCHEMA}.device",
        f"{SCHEMA}.pass",
        f"{SCHEMA}.registration",
    ]


def test_no_table_is_left_to_search_path():
    assert {table.schema for table in metadata.tables.values()} == {SCHEMA}


def test_tables_are_not_registered_on_the_global_singleton():
    # SQLModel.metadata is process-wide. A table registered there would be
    # created into another package's migration run.
    assert not any(name.endswith((".device", ".pass", ".registration")) for name in SQLModel.metadata.tables)


def test_pass_data_is_withdrawn():
    assert not hasattr(db_models, "ApplePassData")


def test_registration_has_the_hot_path_index():
    registration = metadata.tables[f"{SCHEMA}.registration"]
    indexed = {tuple(column.name for column in index.columns) for index in registration.indexes}
    assert ("device_library_identifier", "pass_type_identifier") in indexed


def test_update_tag_sequence_is_created_with_the_schema():
    assert isinstance(db_models.UPDATE_TAG_SEQUENCE, sa.Sequence)
    assert db_models.UPDATE_TAG_SEQUENCE.schema == SCHEMA
    assert db_models.UPDATE_TAG_SEQUENCE.metadata is metadata
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'edutap.wallet_apple_vas_web_service.base'`

- [ ] **Step 3: Write `base.py`**

```python
# src/edutap/wallet_apple_vas_web_service/base.py
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
```

- [ ] **Step 4: Write `db_models.py`**

Replace the file completely.

```python
# src/edutap/wallet_apple_vas_web_service/db_models.py
"""The three tables this service owns.

The shape follows Apple's own storage model in "Adding a Web Service to Update
Passes": two entities, devices and passes, and one many-to-many relationship,
registrations.

The `pass` table holds bookkeeping and no pass content. The content belongs to
the producer that built the pass; this service fetches it at delivery time.
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlmodel import Field

from .base import SCHEMA, Base, metadata


def _utcnow() -> datetime:
    """Timezone-aware now, for the Python-side default."""
    return datetime.now(tz=UTC)


def _timestamp(on_update: bool = False) -> sa.Column:
    """A timestamptz column whose value the database computes."""
    kwargs: dict[str, object] = {"server_default": sa.func.now()}
    if on_update:
        kwargs["onupdate"] = sa.func.now()
    return sa.Column(sa.DateTime(timezone=True), nullable=False, **kwargs)


def _identifier(*args: object, **kwargs: object) -> sa.Column:
    """A column holding an opaque external identifier.

    Byte collation, so comparison and index order do not depend on the
    database's locale — these values are compared for equality by machines,
    never sorted for humans.

    `*args` carries schema constructs that are positional to `Column`, such as
    a `ForeignKey`.
    """
    return sa.Column(sa.String(255, collation="C"), *args, **kwargs)


UPDATE_TAG_SEQUENCE = sa.Sequence("update_tag_seq", schema=SCHEMA, metadata=metadata)
"""Source of `PassRecord.last_update_tag`.

A sequence rather than a clock. The notifier runs with several replicas, and
wall clocks on different hosts are not comparable — which is exactly when the
tag has to be.
"""


class Device(Base, table=True):
    """A device that holds at least one updatable pass."""

    __tablename__ = "device"

    device_library_identifier: str = Field(sa_column=_identifier(primary_key=True))
    push_token: str = Field(
        sa_column=sa.Column(sa.String(255), nullable=False),
        description="APNs token. A credential: never logged, never returned by an endpoint.",
    )
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp())
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp(on_update=True))


class PassRecord(Base, table=True):
    """One updatable pass — bookkeeping only, no content.

    Named `PassRecord` rather than `Pass`: `pass` is a Python keyword, and
    `Pass` is already the pass model of `edutap.wallet_apple`.
    """

    __tablename__ = "pass"

    pass_type_identifier: str = Field(sa_column=_identifier(primary_key=True))
    serial_number: str = Field(sa_column=_identifier(primary_key=True))
    last_update_tag: int = Field(
        sa_column=sa.Column(sa.BigInteger, nullable=False),
        description="Rises on every change of content. Apple leaves its contents to us.",
    )
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp())
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp(on_update=True))


class Registration(Base, table=True):
    """One device holding one pass, and how far behind that device is."""

    __tablename__ = "registration"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["pass_type_identifier", "serial_number"],
            [f"{SCHEMA}.pass.pass_type_identifier", f"{SCHEMA}.pass.serial_number"],
            ondelete="CASCADE",
        ),
        # The hot path: every list request filters on exactly these two, and the
        # result is not small — one pass type identifier may cover many pass
        # kinds, so a device can hold a two-digit number of registrations under
        # one of them.
        sa.Index(
            "ix_registration_device_library_identifier",
            "device_library_identifier",
            "pass_type_identifier",
        ),
    )

    device_library_identifier: str = Field(
        sa_column=_identifier(
            sa.ForeignKey(f"{SCHEMA}.device.device_library_identifier", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    pass_type_identifier: str = Field(sa_column=_identifier(primary_key=True))
    serial_number: str = Field(sa_column=_identifier(primary_key=True))
    delivered_tag: int | None = Field(
        default=None,
        sa_column=sa.Column(sa.BigInteger, nullable=True),
        description="The tag this device provably holds. Null until the first delivery.",
    )
    last_pushed_at: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True)
    )
    last_delivered_at: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True)
    )
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp())
```

- [ ] **Step 5: Write `dbdef.py`**

```python
# src/edutap/wallet_apple_vas_web_service/dbdef.py
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
```

- [ ] **Step 6: Declare the entry point**

In `pyproject.toml`, after the `[project.optional-dependencies]` block:

```toml
# How `edutap.db_definitions` finds this package's tables. Several packages share
# one database; the tool scans this entry point to check them against each other
# for colliding names and for types left to `search_path`.
#
# It is a *development* dependency and never a runtime one -- `dbdef.py` guards
# the import so a deployment without the tool imports cleanly.
[project.entry-points."edutap.db_definitions"]
schema = "edutap.wallet_apple_vas_web_service.dbdef:definition"
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `make venv && .venv/bin/python -m pytest tests/test_schema.py -v`
Expected: 6 passed

- [ ] **Step 8: Run lint**

Run: `make lint`
Expected: no findings

- [ ] **Step 9: Commit**

```bash
git add src/edutap/wallet_apple_vas_web_service/base.py \
        src/edutap/wallet_apple_vas_web_service/db_models.py \
        src/edutap/wallet_apple_vas_web_service/dbdef.py \
        pyproject.toml tests/test_schema.py
git commit -m "feat: own the three tables Apple's storage model describes

Withdraws ApplePassData: pass content belongs to the producer. Adds a
package-local MetaData in schema wallet_apple_vas, announced to
edutap.db_definitions through an entry point."
```

---

### Task 2: Deriving the authentication token

**Files:**
- Create: `src/edutap/wallet_apple_vas_web_service/tokens.py`
- Modify: `src/edutap/wallet_apple_vas_web_service/config.py:96-113`
- Test: `tests/test_tokens.py`
- Delete: `tests/test_auth.py` (it tests the withdrawn global-token check)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `tokens.derive_token(secret: str, pass_type_identifier: str, serial_number: str) -> str`
  - `tokens.verify_authorization(header: str | None, secrets: Sequence[str], pass_type_identifier: str, serial_number: str) -> bool`
  - `config.AppleWalletWebServiceSettings.authentication_secret: SecretStr | None`
  - `config.AppleWalletWebServiceSettings.previous_authentication_secrets: list[SecretStr]`
  - `config.AppleWalletWebServiceSettings.accepted_secrets() -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tokens.py
"""Derivation and verification of the per-pass authentication token."""

import pytest

from edutap.wallet_apple_vas_web_service.tokens import derive_token, verify_authorization

SECRET = "an-issuer-secret"
OTHER_SECRET = "a-rotated-away-secret"
PTID = "pass.de.lmu.events"
SERIAL = "b2c3d4e5-0000-4000-8000-000000000001"


def test_derivation_is_stable():
    # Apple forbids changing a token after creation, so the same inputs must
    # always give the same value.
    assert derive_token(SECRET, PTID, SERIAL) == derive_token(SECRET, PTID, SERIAL)


def test_derivation_clears_the_minimum_length():
    # edutap.wallet_apple assumes at least 16 characters.
    assert len(derive_token(SECRET, PTID, SERIAL)) >= 16


def test_different_passes_get_different_tokens():
    assert derive_token(SECRET, PTID, SERIAL) != derive_token(SECRET, PTID, "other-serial")
    assert derive_token(SECRET, PTID, SERIAL) != derive_token(SECRET, "pass.de.lmu.ub", SERIAL)


def test_concatenation_cannot_collide():
    # Without a separator that cannot occur in either identifier, ("ab", "c")
    # and ("a", "bc") would hash the same message.
    assert derive_token(SECRET, "ab", "c") != derive_token(SECRET, "a", "bc")


def test_a_correct_header_is_accepted():
    token = derive_token(SECRET, PTID, SERIAL)
    assert verify_authorization(f"ApplePass {token}", [SECRET], PTID, SERIAL) is True


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "ApplePass",
        "Bearer sometoken",
        "ApplePass wrong-token",
        "ApplePass token with spaces",
    ],
)
def test_malformed_or_wrong_headers_are_rejected(header):
    assert verify_authorization(header, [SECRET], PTID, SERIAL) is False


def test_a_token_of_another_pass_is_rejected():
    token = derive_token(SECRET, PTID, "a-different-serial")
    assert verify_authorization(f"ApplePass {token}", [SECRET], PTID, SERIAL) is False


def test_a_previous_secret_is_still_accepted():
    # Apple: devices may still hold a pass carrying the old token.
    token = derive_token(OTHER_SECRET, PTID, SERIAL)
    assert verify_authorization(f"ApplePass {token}", [SECRET, OTHER_SECRET], PTID, SERIAL) is True


def test_without_configured_secrets_everything_is_rejected():
    token = derive_token(SECRET, PTID, SERIAL)
    assert verify_authorization(f"ApplePass {token}", [], PTID, SERIAL) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named '…tokens'`

- [ ] **Step 3: Write `tokens.py`**

```python
# src/edutap/wallet_apple_vas_web_service/tokens.py
"""The per-pass authentication token: how it is derived and how it is checked.

Apple states the purpose: the token "shows that the request for an update to a
pass is coming from the user who has the pass and not from a third party". An
issuer-wide token cannot make that statement — a `.pkpass` is a ZIP, every
holder can read the token out of `pass.json`, and the delivery endpoint returns
the full pass to whoever presents it with a serial number.

The value is derived rather than stored. Both sides hold the same issuer secret:
the producer computes the token when it builds the pass, this service computes
it when it verifies. That needs no write path from the producer into this
service, and it authenticates a registration for a pass this service has never
heard of — which is the ordinary case when a freshly issued pass is installed.
"""

import hmac
from collections.abc import Sequence
from hashlib import sha256

SCHEME = "ApplePass"

_SEPARATOR = b"\x00"
"""Separates the two identifiers in the derived message.

A byte that occurs in neither a pass type identifier nor a serial number, so no
two different pairs can produce the same message by concatenation.
"""


def derive_token(secret: str, pass_type_identifier: str, serial_number: str) -> str:
    """Return the authentication token of one pass, as lowercase hex."""
    message = pass_type_identifier.encode("utf-8") + _SEPARATOR + serial_number.encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, sha256).hexdigest()


def verify_authorization(
    header: str | None,
    secrets: Sequence[str],
    pass_type_identifier: str,
    serial_number: str,
) -> bool:
    """Check an `Authorization: ApplePass <token>` header against the secrets.

    With no secret configured every request is rejected: a deployment that
    forgot the value must fail closed rather than accept anything.
    """
    if not header or not secrets:
        return False
    scheme, _, presented = header.partition(" ")
    if scheme != SCHEME or not presented or " " in presented:
        return False

    # Every secret is tried and none of them exits early. Returning on the first
    # match would make the response time say which secret matched, and therefore
    # how long ago the pass was built.
    accepted = False
    for secret in secrets:
        expected = derive_token(secret, pass_type_identifier, serial_number)
        accepted |= hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
    return accepted
```

- [ ] **Step 4: Replace the token settings**

In `config.py`, inside `AppleWalletWebServiceSettings`, replace the line
`authentication_token: str | None = None` with:

```python
    authentication_secret: SecretStr | None = None
    """The issuer secret every pass token is derived from.

    One value, shared with the producer that builds the passes. Arrives from the
    vault, or as a Docker secret through the `_FILE` convention above.
    """

    previous_authentication_secrets: list[SecretStr] = Field(default_factory=list)
    """Secrets rotated away from, newest first.

    Apple: "there may still be devices with the old pass and the old
    authentication token. Your server would have to check the authentication
    token against the list of every token that has ever been valid." A pass
    picks up the new token at its next rebuild; until then its device
    authenticates with an entry from this list.

    From the environment as a JSON array, which is how pydantic-settings parses
    a complex type: `["older-secret", "oldest-secret"]`.
    """

    def accepted_secrets(self) -> list[str]:
        """Return every secret a presented token may have been derived from."""
        secrets = [self.authentication_secret, *self.previous_authentication_secrets]
        return [secret.get_secret_value() for secret in secrets if secret is not None]
```

Add `SecretStr` to the `pydantic` import at the top of the file, which already
imports `Field` and `HttpUrl`.

- [ ] **Step 5: Remove the superseded test file**

```bash
git rm tests/test_auth.py
```

It exercises `check_authentification_token`, which Task 4 removes.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tokens.py -v`
Expected: 14 passed

- [ ] **Step 7: Run lint**

Run: `make lint`
Expected: no findings

- [ ] **Step 8: Commit**

```bash
git add src/edutap/wallet_apple_vas_web_service/tokens.py \
        src/edutap/wallet_apple_vas_web_service/config.py tests/test_tokens.py
git commit -m "feat: derive one authentication token per pass

Replaces the single issuer-wide token. Derived by HMAC from an issuer secret
shared with the producer, so no token has to be stored and a registration for
an unknown pass still authenticates. Rotation keeps previous secrets valid."
```

---

### Task 3: Session without DDL

**Files:**
- Create: `src/edutap/wallet_apple_vas_web_service/session.py`
- Create: `tests/conftest.py`
- Modify: `src/edutap/wallet_apple_vas_web_service/db_models.py` (remove `init_model`, `get_session`)
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `base.metadata`.
- Produces:
  - `session.get_engine(settings: AppleWalletWebServiceSettings) -> Engine`
  - `session.get_session() -> Generator[Session, None, None]` — the FastAPI dependency
  - `conftest.db_session` — an `integration`-marked fixture yielding a `Session`
    against a schema created and dropped per test module

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session.py
"""The session dependency creates no schema."""

import inspect

from edutap.wallet_apple_vas_web_service import session


def test_the_request_path_issues_no_ddl():
    # DDL belongs to the migration container. `create_all` on a request meant
    # every request raced every other one, and it made the service the owner of
    # a schema it is only a user of.
    source = inspect.getsource(session)
    assert "create_all" not in source
    assert "drop_all" not in source


def test_the_engine_is_reused_across_calls():
    # An engine owns a connection pool. One per request is a new pool per
    # request, which is how a service runs out of database connections under
    # exactly the load it was scaled for.
    url = "postgresql+psycopg2://example:example@localhost/example"
    session.get_engine.cache_clear()
    assert session.get_engine(url) is session.get_engine(url)


def test_the_settings_render_a_usable_url():
    from edutap.wallet_apple_vas_web_service.config import DatabaseSettings

    settings = DatabaseSettings(host="db.invalid", name="wallet", username="u", password="p")
    assert settings.url() == "postgresql+psycopg2://u:p@db.invalid/wallet"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_session.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named '…session'`

- [ ] **Step 3: Add the DSN builder to `DatabaseSettings`**

In `config.py`, inside `DatabaseSettings`:

```python
    def url(self) -> str:
        """Return the SQLAlchemy URL for these settings."""
        port = f":{self.port}" if self.port != 5432 else ""
        return (
            f"{self.type}+{self.driver}://{self.username}:{self.password}"
            f"@{self.host}{port}/{self.name}"
        )
```

- [ ] **Step 4: Write `session.py`**

```python
# src/edutap/wallet_apple_vas_web_service/session.py
"""The database engine and the per-request session.

No DDL. The schema is created and migrated by the migration container, which is
the only thing that holds the rights to do it and the only thing that records
what it did. The previous implementation called `create_all` inside the request
dependency, so every request raced every other one and the service silently
owned a schema it is only a user of.
"""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from .config import AppleWalletWebServiceSettings


@lru_cache(maxsize=8)
def get_engine(url: str) -> Engine:
    """Return the process-wide engine for one URL.

    Cached by URL, not rebuilt per request: an engine owns a connection pool,
    and one per request is a new pool per request.

    No `echo`. It writes every statement including its bound parameters, which
    here are push tokens and pass serial numbers.
    """
    return create_engine(url, pool_pre_ping=True)


def get_session() -> Generator[Session, None, None]:
    """Yield one session per request; used as a FastAPI dependency."""
    engine = get_engine(AppleWalletWebServiceSettings().db.url())
    with Session(engine) as session:
        yield session
```

- [ ] **Step 5: Strip `db_models.py`**

Delete `init_model` and `get_session` from `db_models.py`, together with the
now-unused imports of `Generator`, `Session`, `create_engine` and
`AppleWalletWebServiceSettings`. The module holds table declarations only.

- [ ] **Step 6: Write the integration fixture**

```python
# tests/conftest.py
"""Shared fixtures. The database ones are integration-only."""

import os

import pytest
import sqlalchemy as sa
from sqlmodel import Session

from edutap.wallet_apple_vas_web_service.base import SCHEMA, metadata

DSN_VARIABLE = "WALLET_APPLE_VAS_WEB_SERVICE_TEST_DSN"


@pytest.fixture(scope="module")
def engine():
    """An engine against a throwaway database; skips when none is configured."""
    dsn = os.environ.get(DSN_VARIABLE)
    if not dsn:
        pytest.skip(f"{DSN_VARIABLE} is not set")
    engine = sa.create_engine(dsn)
    with engine.begin() as connection:
        connection.execute(sa.schema.CreateSchema(SCHEMA, if_not_exists=True))
    metadata.create_all(engine)
    yield engine
    metadata.drop_all(engine)
    with engine.begin() as connection:
        connection.execute(sa.schema.DropSchema(SCHEMA, cascade=True, if_exists=True))
    engine.dispose()


@pytest.fixture
def db_session(engine):
    """A session rolled back after each test, so tests cannot see each other."""
    connection = engine.connect()
    transaction = connection.begin()
    with Session(bind=connection) as session:
        yield session
    transaction.rollback()
    connection.close()
```

Note: the fixture calls `create_all` deliberately — a test owns its throwaway
database. The rule in Step 4 is about the *request path*, which is what
`test_session.py` asserts.

- [ ] **Step 7: Register the marker**

In `pyproject.toml`, under `[tool.pytest.ini_options]`, add:

```toml
markers = [
    "integration: needs a reachable PostgreSQL, see WALLET_APPLE_VAS_WEB_SERVICE_TEST_DSN",
]
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_session.py -v`
Expected: 3 passed

- [ ] **Step 9: Commit**

```bash
git add src/edutap/wallet_apple_vas_web_service/session.py \
        src/edutap/wallet_apple_vas_web_service/db_models.py \
        src/edutap/wallet_apple_vas_web_service/config.py \
        tests/conftest.py tests/test_session.py pyproject.toml
git commit -m "refactor: move the session out of db_models and stop issuing DDL

create_all ran inside the request dependency, so every request raced every
other one. Schema creation belongs to the migration container."
```

---

### Task 4: Registration

**Files:**
- Modify: `src/edutap/wallet_apple_vas_web_service/service.py:36-176` (replace
  `check_authentification_token` and `register_pass`)
- Test: `tests/test_register.py`

**Interfaces:**
- Consumes: `tokens.verify_authorization`, `db_models.Device`,
  `db_models.PassRecord`, `db_models.Registration`,
  `db_models.UPDATE_TAG_SEQUENCE`, `session.get_session`,
  `config.AppleWalletWebServiceSettings.accepted_secrets`.
- Produces: `service.next_update_tag(session: Session) -> int`,
  `service.router` with the register route.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_register.py
"""Registering a pass for update notifications."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from edutap.wallet_apple_vas_web_service.db_models import Device, PassRecord, Registration
from edutap.wallet_apple_vas_web_service.tokens import derive_token

pytestmark = pytest.mark.integration

SECRET = "an-issuer-secret"
DEVICE = "1a2b3c4d5e6f"
PTID = "pass.de.lmu.events"
SERIAL = "b2c3d4e5-0000-4000-8000-000000000001"


def _url(device: str = DEVICE, ptid: str = PTID, serial: str = SERIAL) -> str:
    return f"/apple_update_service/v1/devices/{device}/registrations/{ptid}/{serial}"


def _auth(secret: str = SECRET, ptid: str = PTID, serial: str = SERIAL) -> dict[str, str]:
    return {"Authorization": f"ApplePass {derive_token(secret, ptid, serial)}"}


def test_a_first_registration_answers_201(client: TestClient):
    response = client.post(_url(), headers=_auth(), json={"pushToken": "a-push-token"})
    assert response.status_code == 201


def test_a_first_registration_creates_all_three_rows(client: TestClient, db_session):
    client.post(_url(), headers=_auth(), json={"pushToken": "a-push-token"})
    assert db_session.get(Device, DEVICE) is not None
    # Apple: "Create a new entry for the pass if one doesn't exist." The pass is
    # registered before this service has ever heard of it.
    assert db_session.get(PassRecord, (PTID, SERIAL)) is not None
    assert db_session.exec(select(Registration)).one() is not None


def test_a_repeated_registration_answers_200(client: TestClient):
    client.post(_url(), headers=_auth(), json={"pushToken": "a-push-token"})
    response = client.post(_url(), headers=_auth(), json={"pushToken": "a-push-token"})
    assert response.status_code == 200


def test_a_repeated_registration_refreshes_the_push_token(client: TestClient, db_session):
    client.post(_url(), headers=_auth(), json={"pushToken": "the-first-token"})
    client.post(_url(), headers=_auth(), json={"pushToken": "the-second-token"})
    assert db_session.get(Device, DEVICE).push_token == "the-second-token"


def test_a_wrong_token_answers_401(client: TestClient):
    response = client.post(
        _url(), headers={"Authorization": "ApplePass wrong"}, json={"pushToken": "t"}
    )
    assert response.status_code == 401


def test_the_token_of_another_pass_answers_401(client: TestClient):
    response = client.post(
        _url(), headers=_auth(serial="a-different-serial"), json={"pushToken": "t"}
    )
    assert response.status_code == 401


def test_a_missing_body_answers_400(client: TestClient):
    response = client.post(_url(), headers=_auth())
    assert response.status_code == 400
```

- [ ] **Step 2: Add the `client` fixture**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def client(db_session):
    """A TestClient whose session, secrets and producer are the test's own."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from edutap.wallet_apple_vas_web_service import service
    from edutap.wallet_apple_vas_web_service.config import (
        AppleWalletWebServiceSettings,
        get_settings,
    )

    settings = AppleWalletWebServiceSettings(
        authentication_secret="an-issuer-secret",
        producer_pass_url_template=(
            "https://builder.invalid/api/v1/passes/"
            "{pass_type_identifier}/{serial_number}"
        ),
        producer_api_token="a-producer-token",
    )

    app = FastAPI()
    app.include_router(service.router)
    app.dependency_overrides[service.get_session] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_register.py -v -m integration`
Expected: FAIL — `register_pass` still writes the old tables

- [ ] **Step 4: Replace the authorization helper and the register route**

In `service.py`, delete `check_authentification_token` entirely and replace the
register route with:

```python
def next_update_tag(session: Session) -> int:
    """Return the next value of the update-tag sequence.

    Through the `Sequence` construct rather than a hand-written `nextval(...)`:
    the sequence lives in this package's schema, and a bare name would resolve
    through `search_path` to whatever the connecting role happens to look at.
    """
    return session.execute(sa.select(UPDATE_TAG_SEQUENCE.next_value())).scalar_one()


def _authorized(
    authorization: str | None,
    settings: AppleWalletWebServiceSettings,
    pass_type_identifier: str,
    serial_number: str,
) -> bool:
    """Whether this request carries the authentication token of this pass."""
    if not settings.auth_required:
        return True
    return verify_authorization(
        authorization, settings.accepted_secrets(), pass_type_identifier, serial_number
    )


@router.post("/devices/{deviceLibraryIdentifier}/registrations/{passTypeIdentifier}/{serialNumber}")
async def register_pass(
    deviceLibraryIdentifier: str,
    passTypeIdentifier: str,
    serialNumber: str,
    authorization: Annotated[str | None, Header()] = None,
    data: AppleWalletWebServiceAuthorizationPayload | None = None,
    *,
    settings: AppleWalletWebServiceSettings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> Response:
    """Register a device to receive update notifications for a pass.

    https://developer.apple.com/documentation/walletpasses/register-a-pass-for-update-notifications

    Apple's tasks for this call: create the pass entry if it does not exist,
    create the device entry if it does not exist, store the mapping between
    them. Answers 201 on a new registration, 200 when the serial number was
    already registered for the device, 401 when unauthorized.
    """
    if not _authorized(authorization, settings, passTypeIdentifier, serialNumber):
        return Response(status_code=401)

    # A missing body is a bad request, not an assertion: `assert` disappears
    # under `python -O`, and a None would reach `data.pushToken` below and turn
    # a malformed registration into a 500.
    if data is None:
        return Response(status_code=400)

    device = session.get(Device, deviceLibraryIdentifier)
    if device is None:
        session.add(Device(device_library_identifier=deviceLibraryIdentifier, push_token=data.pushToken))
    elif device.push_token != data.pushToken:
        # A device that re-registers with a new token has moved; pushing to the
        # old one would silently reach nobody.
        device.push_token = data.pushToken
        session.add(device)

    if session.get(PassRecord, (passTypeIdentifier, serialNumber)) is None:
        session.add(
            PassRecord(
                pass_type_identifier=passTypeIdentifier,
                serial_number=serialNumber,
                last_update_tag=next_update_tag(session),
            )
        )

    existing = session.get(Registration, (deviceLibraryIdentifier, passTypeIdentifier, serialNumber))
    if existing is not None:
        session.commit()
        return Response(status_code=200)

    session.add(
        Registration(
            device_library_identifier=deviceLibraryIdentifier,
            pass_type_identifier=passTypeIdentifier,
            serial_number=serialNumber,
        )
    )
    session.commit()
    return Response(status_code=201)
```

Adjust the imports at the top of `service.py`: drop `ApplePassData`,
`AppleDeviceRegistry`, `ApplePassRegistry` and `Pass`; add

```python
import sqlalchemy as sa
from sqlmodel import Session, select

from .db_models import UPDATE_TAG_SEQUENCE, Device, PassRecord, Registration
from .session import get_session
from .tokens import verify_authorization
```

Note the spelling: the path parameter is `deviceLibraryIdentifier`. The old
code misspelt it `deviceLibraryIdentitfier` in every route.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_register.py -v -m integration`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add src/edutap/wallet_apple_vas_web_service/service.py \
        tests/test_register.py tests/conftest.py
git commit -m "feat: register against the new schema

Creates the pass entry when it does not exist, as Apple's task list requires,
and refreshes a push token that changed. Fixes the misspelt path parameter."
```

---

### Task 5: Unregistration

**Files:**
- Modify: `src/edutap/wallet_apple_vas_web_service/service.py` (replace `unregister_pass`)
- Test: `tests/test_unregister.py`

**Interfaces:**
- Consumes: everything Task 4 produces.
- Produces: the delete route on `service.router`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_unregister.py
"""Unregistering a pass for update notifications."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from edutap.wallet_apple_vas_web_service.db_models import Device, Registration
from edutap.wallet_apple_vas_web_service.tokens import derive_token

pytestmark = pytest.mark.integration

SECRET = "an-issuer-secret"
DEVICE = "1a2b3c4d5e6f"
PTID = "pass.de.lmu.events"
FIRST = "serial-one"
SECOND = "serial-two"


def _url(serial: str) -> str:
    return f"/apple_update_service/v1/devices/{DEVICE}/registrations/{PTID}/{serial}"


def _auth(serial: str) -> dict[str, str]:
    return {"Authorization": f"ApplePass {derive_token(SECRET, PTID, serial)}"}


@pytest.fixture
def two_registrations(client: TestClient):
    for serial in (FIRST, SECOND):
        client.post(_url(serial), headers=_auth(serial), json={"pushToken": "a-push-token"})
    return client


def test_unregistering_answers_200(two_registrations: TestClient):
    assert two_registrations.delete(_url(FIRST), headers=_auth(FIRST)).status_code == 200


def test_only_the_named_pass_is_unregistered(two_registrations: TestClient, db_session):
    # The old code joined its conditions with `and`, which SQLAlchemy reduces to
    # the first one -- so a single unregistration deleted every registration of
    # the device.
    two_registrations.delete(_url(FIRST), headers=_auth(FIRST))
    remaining = db_session.exec(select(Registration)).all()
    assert [row.serial_number for row in remaining] == [SECOND]


def test_the_device_survives_while_it_holds_another_pass(two_registrations: TestClient, db_session):
    two_registrations.delete(_url(FIRST), headers=_auth(FIRST))
    assert db_session.get(Device, DEVICE) is not None


def test_the_device_is_removed_with_its_last_registration(two_registrations: TestClient, db_session):
    # Apple: "Delete the device entry from the device table if the registration
    # table has no more entries for that device."
    for serial in (FIRST, SECOND):
        two_registrations.delete(_url(serial), headers=_auth(serial))
    assert db_session.get(Device, DEVICE) is None


def test_an_unknown_registration_answers_200(client: TestClient):
    # Apple documents only 200 and 401 for this endpoint. The old code answered
    # 404, which is not in the contract, and the outcome the caller asked for --
    # this device is not registered for this pass -- has been reached either way.
    assert client.delete(_url(FIRST), headers=_auth(FIRST)).status_code == 200


def test_a_wrong_token_answers_401(two_registrations: TestClient):
    response = two_registrations.delete(_url(FIRST), headers={"Authorization": "ApplePass wrong"})
    assert response.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_unregister.py -v -m integration`
Expected: FAIL — the route still queries the withdrawn tables

- [ ] **Step 3: Replace the route**

```python
@router.delete("/devices/{deviceLibraryIdentifier}/registrations/{passTypeIdentifier}/{serialNumber}")
async def unregister_pass(
    deviceLibraryIdentifier: str,
    passTypeIdentifier: str,
    serialNumber: str,
    authorization: Annotated[str | None, Header()] = None,
    *,
    settings: AppleWalletWebServiceSettings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> Response:
    """Stop sending update notifications for a pass on a device.

    https://developer.apple.com/documentation/walletpasses/unregister-a-pass-for-update-notifications

    Apple's tasks: delete the mapping, then delete the device if no registration
    is left for it. Answers 200 or 401 — the two codes Apple documents.
    """
    if not _authorized(authorization, settings, passTypeIdentifier, serialNumber):
        return Response(status_code=401)

    registration = session.get(
        Registration, (deviceLibraryIdentifier, passTypeIdentifier, serialNumber)
    )
    if registration is not None:
        session.delete(registration)
        session.flush()

    remaining = session.exec(
        select(Registration).where(
            Registration.device_library_identifier == deviceLibraryIdentifier
        )
    ).first()
    if remaining is None:
        device = session.get(Device, deviceLibraryIdentifier)
        if device is not None:
            session.delete(device)

    session.commit()
    return Response(status_code=200)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_unregister.py -v -m integration`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/edutap/wallet_apple_vas_web_service/service.py tests/test_unregister.py
git commit -m "fix: unregister only the named pass, and clean up the device

The conditions were joined with `and`, which SQLAlchemy reduces to the first
one -- one unregistration deleted every registration of the device."
```

---

### Task 6: The list of updatable passes

**Files:**
- Modify: `src/edutap/wallet_apple_vas_web_service/http_models.py` (`SerialNumbers`)
- Modify: `src/edutap/wallet_apple_vas_web_service/service.py` (replace `update_pass`)
- Test: `tests/test_updatable_list.py`

**Interfaces:**
- Consumes: everything Task 4 produces.
- Produces: `http_models.SerialNumbers(serialNumbers: list[str], lastUpdated: str)`
  and the list route on `service.router`.

- [ ] **Step 1: Fix the response model**

In `http_models.py`, rename the misspelt field and type both fields as Apple
documents them:

```python
class SerialNumbers(BaseModel):
    """The body of a successful list response.

    https://developer.apple.com/documentation/walletpasses/serialnumbers

    `lastUpdated` is a string. Apple's example shows `"1351901923"`, and the
    device stores the value opaquely and returns it unchanged.
    """

    serialNumbers: list[str]
    lastUpdated: str
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_updatable_list.py
"""The list of updatable passes for one device."""

import pytest
from fastapi.testclient import TestClient

from edutap.wallet_apple_vas_web_service.db_models import PassRecord, Registration
from edutap.wallet_apple_vas_web_service.tokens import derive_token

pytestmark = pytest.mark.integration

SECRET = "an-issuer-secret"
DEVICE = "1a2b3c4d5e6f"
PTID = "pass.de.lmu.events"
SERIAL = "serial-one"

LIST_URL = f"/apple_update_service/v1/devices/{DEVICE}/registrations/{PTID}"


@pytest.fixture
def registered(client: TestClient):
    url = f"/apple_update_service/v1/devices/{DEVICE}/registrations/{PTID}/{SERIAL}"
    client.post(
        url,
        headers={"Authorization": f"ApplePass {derive_token(SECRET, PTID, SERIAL)}"},
        json={"pushToken": "a-push-token"},
    )
    return client


def test_the_endpoint_needs_no_authorization(registered: TestClient):
    # Apple: the second shared secret "is the device library ID". This endpoint
    # carries no Authorization header and documents no 401 -- checking a
    # per-pass token here is impossible anyway, the URL has no serial number.
    assert registered.get(LIST_URL).status_code == 200


def test_a_never_delivered_pass_is_listed(registered: TestClient):
    # delivered_tag is null until the first delivery, and `tag > NULL` is null
    # rather than true -- without an explicit arm this pass would never appear.
    body = registered.get(LIST_URL).json()
    assert body["serialNumbers"] == [SERIAL]


def test_the_response_carries_a_string_tag(registered: TestClient):
    assert isinstance(registered.get(LIST_URL).json()["lastUpdated"], str)


def test_a_current_device_gets_204(registered: TestClient, db_session):
    record = db_session.get(PassRecord, (PTID, SERIAL))
    registration = db_session.get(Registration, (DEVICE, PTID, SERIAL))
    registration.delivered_tag = record.last_update_tag
    db_session.add(registration)
    db_session.commit()
    assert registered.get(LIST_URL).status_code == 204


def test_a_changed_pass_is_listed_again(registered: TestClient, db_session):
    registration = db_session.get(Registration, (DEVICE, PTID, SERIAL))
    record = db_session.get(PassRecord, (PTID, SERIAL))
    registration.delivered_tag = record.last_update_tag
    record.last_update_tag = record.last_update_tag + 1
    db_session.add_all([registration, record])
    db_session.commit()
    assert registered.get(LIST_URL).json()["serialNumbers"] == [SERIAL]


def test_our_record_outweighs_a_too_advanced_device_cursor(registered: TestClient):
    # The device cursor can be ahead of a pass whose tag was committed late by
    # another notifier replica. Compared against what this device provably
    # holds, that pass is still ahead and must be listed.
    body = registered.get(LIST_URL, params={"passesUpdatedSince": "999999"}).json()
    assert body["serialNumbers"] == [SERIAL]


def test_both_cursor_names_are_accepted(registered: TestClient):
    # Apple's endpoint page names it previousLastUpdated; the wire has carried
    # passesUpdatedSince for over a decade. Accepting both costs one line.
    for name in ("passesUpdatedSince", "previousLastUpdated"):
        assert registered.get(LIST_URL, params={name: "0"}).status_code == 200


def test_an_unparsable_cursor_is_ignored(registered: TestClient):
    assert registered.get(LIST_URL, params={"passesUpdatedSince": "not-a-number"}).status_code == 200


def test_an_unknown_device_gets_204(client: TestClient):
    unknown = "/apple_update_service/v1/devices/no-such-device/registrations/" + PTID
    assert client.get(unknown).status_code == 204
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_updatable_list.py -v -m integration`
Expected: FAIL — the route still checks a token and queries `ApplePassData`

- [ ] **Step 4: Replace the route**

```python
@router.get("/devices/{deviceLibraryIdentifier}/registrations/{passTypeIdentifier}")
async def list_updatable_passes(
    deviceLibraryIdentifier: str,
    passTypeIdentifier: str,
    passesUpdatedSince: str | None = None,
    previousLastUpdated: str | None = None,
    *,
    session: Session = Depends(get_session),
) -> Response:
    """Send the serial numbers for updated passes to a device.

    https://developer.apple.com/documentation/walletpasses/get-the-list-of-updatable-passes

    No authorization: Apple authenticates this call with the device library
    identifier itself, which is the other of the two shared secrets. The URL
    carries no serial number, so a per-pass token could not be checked here even
    if one were sent.

    Answers 200 with a SerialNumbers body, or 204 when nothing matches.
    """
    cursor = _cursor(passesUpdatedSince, previousLastUpdated)

    rows = session.exec(
        select(Registration, PassRecord)
        .join(
            PassRecord,
            (Registration.pass_type_identifier == PassRecord.pass_type_identifier)
            & (Registration.serial_number == PassRecord.serial_number),
        )
        .where(
            Registration.device_library_identifier == deviceLibraryIdentifier,
            Registration.pass_type_identifier == passTypeIdentifier,
        )
    ).all()

    behind = [
        (registration, record)
        for registration, record in rows
        # Our own record is the precise filter, the device's cursor the safety
        # net. Each closes the other's gap, and neither can shorten the list --
        # the one error this endpoint must not make.
        if registration.delivered_tag is None
        or record.last_update_tag > registration.delivered_tag
        or cursor is None
        or record.last_update_tag > cursor
    ]
    if not behind:
        return Response(status_code=204)

    payload = SerialNumbers(
        serialNumbers=[record.serial_number for _, record in behind],
        lastUpdated=str(max(record.last_update_tag for _, record in behind)),
    )
    return Response(
        payload.model_dump_json(), status_code=200, media_type="application/json"
    )
```

And the helper, above the route:

```python
def _cursor(*candidates: str | None) -> int | None:
    """Return the first parsable cursor value, or None.

    Apple's current endpoint page names the parameter `previousLastUpdated`,
    while the wire has carried `passesUpdatedSince` since the protocol was
    published; see the design document. Both are accepted, and a value that does
    not parse is treated as absent -- an unreadable cursor must widen the answer,
    never narrow it.
    """
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return int(float(candidate))
        except ValueError:
            continue
    return None
```

Add `SerialNumbers` to the `http_models` import in `service.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_updatable_list.py -v -m integration`
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add src/edutap/wallet_apple_vas_web_service/service.py \
        src/edutap/wallet_apple_vas_web_service/http_models.py \
        tests/test_updatable_list.py
git commit -m "fix: answer the updatable list without a token, from the new schema

Apple authenticates this call with the device library identifier; the URL has
no serial number, so a per-pass token cannot be checked here. Filters against
what the device provably holds, with its own cursor as the safety net, and
fixes the misspelt serialNumbers field."
```

---

### Task 7: Delivering a pass

**Files:**
- Create: `src/edutap/wallet_apple_vas_web_service/producer.py`
- Modify: `src/edutap/wallet_apple_vas_web_service/config.py`
- Modify: `src/edutap/wallet_apple_vas_web_service/service.py` (replace `send_updated_pass`)
- Test: `tests/test_producer.py`, `tests/test_delivery.py`

**Interfaces:**
- Consumes: everything Task 4 produces.
- Produces:
  - `producer.ProducerError` — raised when the producer cannot be reached
  - `producer.PassNotAvailable` — raised on 404/410 from the producer
  - `producer.fetch_pass(settings, pass_type_identifier, serial_number) -> bytes`
  - `config.AppleWalletWebServiceSettings.producer_pass_url_template: str | None`
  - `config.AppleWalletWebServiceSettings.producer_api_token: SecretStr | None`
  - `config.AppleWalletWebServiceSettings.producer_timeout_seconds: float`

- [ ] **Step 1: Write the failing test for the client**

```python
# tests/test_producer.py
"""Fetching a built pass from the configured producer."""

import pytest

from edutap.wallet_apple_vas_web_service.config import AppleWalletWebServiceSettings
from edutap.wallet_apple_vas_web_service.producer import (
    PassNotAvailable,
    ProducerError,
    fetch_pass,
)

PTID = "pass.de.lmu.events"
SERIAL = "serial-one"
TEMPLATE = "https://builder.invalid/api/v1/passes/{pass_type_identifier}/{serial_number}"
EXPECTED_URL = f"https://builder.invalid/api/v1/passes/{PTID}/{SERIAL}"


@pytest.fixture
def settings():
    return AppleWalletWebServiceSettings(
        producer_pass_url_template=TEMPLATE,
        producer_api_token="a-producer-token",
    )


def test_the_template_names_the_pass(settings, requests_mock):
    requests_mock.get(EXPECTED_URL, content=b"PK\x03\x04")
    assert fetch_pass(settings, PTID, SERIAL) == b"PK\x03\x04"


def test_the_producer_token_is_sent(settings, requests_mock):
    requests_mock.get(EXPECTED_URL, content=b"PK\x03\x04")
    fetch_pass(settings, PTID, SERIAL)
    assert requests_mock.last_request.headers["Authorization"] == "Bearer a-producer-token"


@pytest.mark.parametrize("status", [404, 410])
def test_a_withdrawn_pass_raises_pass_not_available(settings, requests_mock, status):
    requests_mock.get(EXPECTED_URL, status_code=status)
    with pytest.raises(PassNotAvailable):
        fetch_pass(settings, PTID, SERIAL)


def test_a_failing_producer_raises_producer_error(settings, requests_mock):
    requests_mock.get(EXPECTED_URL, status_code=500)
    with pytest.raises(ProducerError):
        fetch_pass(settings, PTID, SERIAL)


def test_an_unconfigured_producer_raises_producer_error():
    with pytest.raises(ProducerError):
        fetch_pass(AppleWalletWebServiceSettings(), PTID, SERIAL)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_producer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named '…producer'`

- [ ] **Step 3: Add the producer settings**

In `config.py`, inside `AppleWalletWebServiceSettings`:

```python
    producer_pass_url_template: str | None = None
    """Where to fetch a built pass, with `{pass_type_identifier}` and `{serial_number}`.

    A template rather than a base URL, because the retrieval contract with the
    producer is not settled (see the design document). A deployment supplies the
    shape it agreed, and this service hardcodes no assumption about it.
    """

    producer_api_token: SecretStr | None = None
    """Bearer token this service presents to its producer."""

    producer_timeout_seconds: float = 10.0
    """How long to wait for a built pass. Apple's device is waiting behind it."""
```

- [ ] **Step 4: Write `producer.py`**

```python
# src/edutap/wallet_apple_vas_web_service/producer.py
"""Fetching a built pass from the one producer this deployment is configured with.

The pass content belongs to whoever built it. This service holds registrations
and asks for the current pass by Apple's key alone -- it knows no person, no
template and no validity, and it resolves nothing at runtime: there is exactly
one producer per deployment, named in configuration.
"""

import requests

from .config import AppleWalletWebServiceSettings


class ProducerError(Exception):
    """The producer could not be reached, or answered in a way we cannot use."""


class PassNotAvailable(ProducerError):
    """The producer knows this pass and will not hand it out."""


def fetch_pass(
    settings: AppleWalletWebServiceSettings,
    pass_type_identifier: str,
    serial_number: str,
) -> bytes:
    """Return the current `.pkpass` for one pass."""
    if not settings.producer_pass_url_template:
        raise ProducerError("No producer configured: producer_pass_url_template is unset.")

    url = settings.producer_pass_url_template.format(
        pass_type_identifier=pass_type_identifier, serial_number=serial_number
    )
    headers = {}
    if settings.producer_api_token is not None:
        headers["Authorization"] = f"Bearer {settings.producer_api_token.get_secret_value()}"

    try:
        response = requests.get(url, headers=headers, timeout=settings.producer_timeout_seconds)
    except requests.RequestException as error:
        # The URL is not repeated in the message: it is built from settings that
        # carry no secret, but the exception travels into logs and error
        # trackers, and the token is in the headers of the request object the
        # original exception references.
        raise ProducerError("The producer is not reachable.") from None

    if response.status_code in (404, 410):
        raise PassNotAvailable(f"The producer does not serve {serial_number!r}.")
    if response.status_code != 200:
        raise ProducerError(f"The producer answered {response.status_code}.")
    return response.content
```

- [ ] **Step 5: Run the client tests**

Run: `.venv/bin/python -m pytest tests/test_producer.py -v`
Expected: 6 passed

- [ ] **Step 6: Write the failing test for the endpoint**

```python
# tests/test_delivery.py
"""Delivering an updated pass, and recording that the device collected it."""

import pytest
from fastapi.testclient import TestClient

from edutap.wallet_apple_vas_web_service.db_models import PassRecord, Registration
from edutap.wallet_apple_vas_web_service.tokens import derive_token

pytestmark = pytest.mark.integration

SECRET = "an-issuer-secret"
DEVICE = "1a2b3c4d5e6f"
PTID = "pass.de.lmu.events"
SERIAL = "serial-one"

DELIVERY_URL = f"/apple_update_service/v1/passes/{PTID}/{SERIAL}"
PRODUCER_URL = f"https://builder.invalid/api/v1/passes/{PTID}/{SERIAL}"


def _auth() -> dict[str, str]:
    return {"Authorization": f"ApplePass {derive_token(SECRET, PTID, SERIAL)}"}


@pytest.fixture
def registered(client: TestClient):
    url = f"/apple_update_service/v1/devices/{DEVICE}/registrations/{PTID}/{SERIAL}"
    client.post(url, headers=_auth(), json={"pushToken": "a-push-token"})
    return client


def test_a_built_pass_is_returned(registered: TestClient, requests_mock):
    requests_mock.get(PRODUCER_URL, content=b"PK\x03\x04")
    response = registered.get(DELIVERY_URL, headers=_auth())
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.apple.pkpass"
    assert response.content == b"PK\x03\x04"


def test_delivery_records_what_the_device_now_holds(registered: TestClient, requests_mock, db_session):
    requests_mock.get(PRODUCER_URL, content=b"PK\x03\x04")
    registered.get(DELIVERY_URL, headers=_auth())
    db_session.expire_all()
    registration = db_session.get(Registration, (DEVICE, PTID, SERIAL))
    record = db_session.get(PassRecord, (PTID, SERIAL))
    assert registration.delivered_tag == record.last_update_tag
    assert registration.last_delivered_at is not None


def test_a_wrong_token_answers_401_without_asking_the_producer(registered: TestClient, requests_mock):
    matcher = requests_mock.get(PRODUCER_URL, content=b"PK\x03\x04")
    response = registered.get(DELIVERY_URL, headers={"Authorization": "ApplePass wrong"})
    assert response.status_code == 401
    assert matcher.call_count == 0


def test_an_unreachable_producer_answers_503(registered: TestClient, requests_mock):
    requests_mock.get(PRODUCER_URL, status_code=500)
    assert registered.get(DELIVERY_URL, headers=_auth()).status_code == 503


def test_a_withdrawn_pass_answers_401(registered: TestClient, requests_mock):
    # Apple documents only 200 and 401 for this endpoint, so a pass the producer
    # refuses cannot be reported as 404 or 410 without leaving the contract.
    requests_mock.get(PRODUCER_URL, status_code=410)
    assert registered.get(DELIVERY_URL, headers=_auth()).status_code == 401


def test_delivery_to_an_unregistered_device_still_works(client: TestClient, requests_mock):
    # A device may fetch a pass it never registered; Apple's flow does not
    # require registration before delivery. Nothing is recorded in that case.
    requests_mock.get(PRODUCER_URL, content=b"PK\x03\x04")
    assert client.get(DELIVERY_URL, headers=_auth()).status_code == 200
```

- [ ] **Step 7: Replace the delivery route**

```python
@router.get("/passes/{passTypeIdentifier}/{serialNumber}")
async def send_updated_pass(
    passTypeIdentifier: str,
    serialNumber: str,
    authorization: Annotated[str | None, Header()] = None,
    *,
    settings: AppleWalletWebServiceSettings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> Response:
    """Create and sign an updated pass, and send it to the device.

    https://developer.apple.com/documentation/walletpasses/send-an-updated-pass

    The pass is built by the configured producer, which resolves the person and
    reads current data. This service passes Apple's key through and records what
    the device now holds.

    Apple documents 200 and 401 for this endpoint and nothing else, so a pass
    the producer refuses is answered 401 rather than 404 or 410.
    """
    if not _authorized(authorization, settings, passTypeIdentifier, serialNumber):
        return Response(status_code=401)

    try:
        content = fetch_pass(settings, passTypeIdentifier, serialNumber)
    except PassNotAvailable:
        return Response(status_code=401)
    except ProducerError:
        LOGGER.exception("The producer could not deliver %s/%s", passTypeIdentifier, serialNumber)
        return Response(status_code=503)

    record = session.get(PassRecord, (passTypeIdentifier, serialNumber))
    if record is not None:
        delivered_at = datetime.now(tz=UTC)
        # Apple's delivery URL carries no device identifier, so which device is
        # asking cannot be known here. Every registration of this pass that is
        # behind is recorded as current -- see "Known limitation" in the plan
        # this route comes from.
        for registration in session.exec(
            select(Registration).where(
                Registration.pass_type_identifier == passTypeIdentifier,
                Registration.serial_number == serialNumber,
            )
        ).all():
            if registration.delivered_tag != record.last_update_tag:
                registration.delivered_tag = record.last_update_tag
                registration.last_delivered_at = delivered_at
                session.add(registration)
        session.commit()

    return Response(
        content,
        status_code=200,
        media_type="application/vnd.apple.pkpass",
        headers={"Content-Disposition": f'attachment; filename="{serialNumber}.pkpass"'},
    )
```

Add to the imports of `service.py`:

```python
from datetime import UTC, datetime

from .producer import PassNotAvailable, ProducerError, fetch_pass
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_delivery.py -v -m integration`
Expected: 6 passed

- [ ] **Step 9: Run the whole suite and lint**

Run: `make test-local && make test-integration && make lint`
Expected: all green

- [ ] **Step 10: Commit**

```bash
git add src/edutap/wallet_apple_vas_web_service/producer.py \
        src/edutap/wallet_apple_vas_web_service/service.py \
        src/edutap/wallet_apple_vas_web_service/config.py \
        tests/test_producer.py tests/test_delivery.py
git commit -m "feat: deliver passes from the configured producer

The service holds no pass content. It asks one configured producer by Apple's
key, and records the tag the device now provably holds."
```

---

## Known limitation carried out of this plan

`send_updated_pass` receives no device identifier — Apple's delivery URL is
`/v1/passes/{passTypeIdentifier}/{serialNumber}` and nothing more. The route
above therefore advances `delivered_tag` for every registration of that pass
that is behind, not only for the device that actually asked.

With one device this is exact. With an iPhone and a Watch registered for the
same pass, the first fetch marks both as current, and the second device can be
recorded as holding a pass it has not collected — the failure mode the design
names for a lost response, reached by a different route.

This is not solvable from Apple's request alone. Two candidates for the
follow-up plan, neither decided here: infer the device from the push that was
sent last (`last_pushed_at`), or accept the imprecision and let the device's own
cursor carry the correction, which it does. **Record it in the notifier plan and
decide it there** — that plan owns `last_pushed_at`.

## Not covered here

From the spec's section 9, unchanged and still open:

- The retrieval contract with the producer. This plan makes it a configured URL
  template so no assumption is compiled in, but the shape has to be agreed with
  the producer before a deployment works.
- Observing what a device actually sends as the cursor.
- Deactivation mechanics (`voided`).
- `passTypeIdentifier` in `public.pass_state`.
- The legal reference for the PID uniqueness rule.
- The move to async: this plan keeps the synchronous `requests` and SQLModel the
  package already uses, so it changes one thing at a time.
