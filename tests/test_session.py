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
