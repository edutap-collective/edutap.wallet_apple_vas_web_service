"""The session dependency creates no schema."""

import contextlib
import os

import pytest
import sqlalchemy as sa

from edutap.wallet_apple_vas_web_service import session

# Kept in sync with `conftest.DSN_VARIABLE` by hand rather than imported: `tests/`
# has no `__init__.py`, so it is not reliably importable as a package.
DSN_VARIABLE = "WALLET_APPLE_VAS_WEB_SERVICE_TEST_DSN"


@pytest.mark.integration
def test_get_session_issues_no_ddl(monkeypatch):
    """Driving `get_session()` against a real database emits no DDL.

    A grep over the module source -- this test's previous shape -- proves
    nothing: it is satisfied by rewording a comment that merely *describes*
    DDL (which is exactly what happened while building this task), and it is
    blind to `Table.create()`, a raw `CREATE TABLE`, or an Alembic call. Only
    running the dependency and recording what it actually sends to the
    database proves the request path stays DDL-free.

    This also exercises `get_session()` itself, which nothing else in this
    suite calls -- only `get_engine` and `DatabaseSettings.url()` in
    isolation. A typo in the `AppleWalletWebServiceSettings().db.url()`
    wiring would otherwise not surface until an endpoint calls it in Task 4.
    """
    from edutap.wallet_apple_vas_web_service.config import AppleWalletWebServiceSettings

    dsn = os.environ.get(DSN_VARIABLE)
    if not dsn:
        pytest.skip(f"{DSN_VARIABLE} is not set")
    url = sa.engine.make_url(dsn)

    # `get_session()` builds its URL from `AppleWalletWebServiceSettings().db`,
    # not from the test DSN string directly, so the settings have to be pointed
    # at the same database through their own environment variables.
    monkeypatch.setenv("EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_DB_TYPE", url.get_backend_name())
    monkeypatch.setenv("EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_DB_DRIVER", url.get_driver_name())
    monkeypatch.setenv("EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_DB_HOST", url.host or "")
    monkeypatch.setenv("EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_DB_PORT", str(url.port or 5432))
    monkeypatch.setenv("EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_DB_NAME", url.database or "")
    monkeypatch.setenv("EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_DB_USERNAME", url.username or "")
    monkeypatch.setenv("EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_DB_PASSWORD", url.password or "")

    session.get_engine.cache_clear()
    engine = session.get_engine(AppleWalletWebServiceSettings().db.url())

    statements: list[str] = []

    def record_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sa.event.listen(engine, "before_cursor_execute", record_statement)
    try:
        generator = session.get_session()
        db = next(generator)
        db.execute(sa.text("SELECT 1"))
        # Exhaust the generator the way FastAPI does when a request ends, so the
        # `with Session(engine) as session:` block in `get_session()` runs its exit.
        with contextlib.suppress(StopIteration):
            next(generator)
    finally:
        sa.event.remove(engine, "before_cursor_execute", record_statement)
        session.get_engine.cache_clear()

    assert statements, "expected at least the SELECT to have been recorded"
    ddl_prefixes = ("CREATE", "DROP", "ALTER")
    assert not any(statement.strip().upper().startswith(ddl_prefixes) for statement in statements)


def test_the_engine_is_reused_across_calls():
    # An engine owns a connection pool. One per request is a new pool per
    # request, which is how a service runs out of database connections under
    # exactly the load it was scaled for.
    #
    # A `URL` object, not the bare string this test built before: `get_engine`'s
    # contract is now `URL` (see `session.py` and `DatabaseSettings.url()`),
    # and `sa.engine.make_url` parses a DSN string into exactly that -- keeping
    # the readable literal here without handing `get_engine` a plaintext DSN
    # string, which is the shape this fix moved away from.
    url = sa.engine.make_url("postgresql+psycopg2://example:example@localhost/example")
    session.get_engine.cache_clear()
    assert session.get_engine(url) is session.get_engine(url)


def test_the_settings_render_a_usable_url():
    from edutap.wallet_apple_vas_web_service.config import DatabaseSettings

    settings = DatabaseSettings(host="db.invalid", name="wallet", username="u", password="p")
    # `render_as_string(hide_password=False)`, not `str(...)` or `==` on the
    # `URL` directly: the whole point of returning a `URL` instead of an
    # f-string is that its default rendering masks the password (see
    # `DatabaseSettings.url()`), so this test has to ask for the unmasked
    # form explicitly to check the value is actually right. Production code
    # never calls `render_as_string(hide_password=False)` -- only this test
    # does, deliberately, because a test has to see the real value to check
    # it, the same reason `SecretStr.get_secret_value()` exists.
    assert (
        settings.url().render_as_string(hide_password=False)
        == "postgresql+psycopg2://u:p@db.invalid/wallet"
    )


def test_the_url_masks_the_password_by_default():
    """`repr()`/`str()` of the `URL` itself must not carry the plaintext password.

    This is the property `DatabaseSettings.url()` exists to hold: unlike the
    f-string DSN it replaced, the value returned here is safe to have as a
    local on a traceback, in a log record, or anywhere else something calls
    `repr()` without knowing it is holding a credential.
    """
    from edutap.wallet_apple_vas_web_service.config import DatabaseSettings

    url = DatabaseSettings(
        host="db.invalid", name="wallet", username="u", password="a-database-secret"
    ).url()
    assert "a-database-secret" not in str(url)
    assert "a-database-secret" not in repr(url)
    assert str(url) == "postgresql+psycopg2://u:***@db.invalid/wallet"
    assert repr(url) == "postgresql+psycopg2://u:***@db.invalid/wallet"


def test_a_bad_url_does_not_leak_the_password_through_frame_locals():
    """No frame reachable from a raised exception carries the database password.

    Same property, same style of check as `producer.py`'s frame-locals
    regression test: an unparsable/unresolvable dialect makes `create_engine`
    fail synchronously, with no network call, the same way `producer.py`'s
    `producer_timeout_seconds=0` reaches a `ValueError` before any request is
    sent. Before this fix, `get_engine` received a plain f-string DSN, which
    *is* the plaintext password -- measured, it showed up as a local named
    `url` in three frames, two of them SQLAlchemy's own. A `URL` object does
    not have this problem even where it does sit in a local: its `repr()` is
    masked, so a `repr()`-based capture tool reads `***`, not the password.
    """
    from edutap.wallet_apple_vas_web_service.config import DatabaseSettings

    bad_url = DatabaseSettings(
        type="not-a-real-dialect",
        driver="not-a-real-driver",
        host="db.invalid",
        name="wallet",
        username="u",
        password="a-database-secret",
    ).url()

    session.get_engine.cache_clear()
    try:
        with pytest.raises(Exception) as excinfo:
            session.get_engine(bad_url)
    finally:
        session.get_engine.cache_clear()

    traceback = excinfo.value.__traceback__
    while traceback is not None:
        for local_value in traceback.tb_frame.f_locals.values():
            assert "a-database-secret" not in repr(local_value)
        traceback = traceback.tb_next
