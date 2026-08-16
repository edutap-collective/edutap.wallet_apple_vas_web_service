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
