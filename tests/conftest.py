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
            "https://builder.invalid/api/v1/passes/{pass_type_identifier}/{serial_number}"
        ),
        producer_api_token="a-producer-token",
    )

    app = FastAPI()
    app.include_router(service.router)
    app.dependency_overrides[service.get_session] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)
