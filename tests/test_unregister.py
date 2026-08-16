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


def test_the_device_is_removed_with_its_last_registration(
    two_registrations: TestClient, db_session
):
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
