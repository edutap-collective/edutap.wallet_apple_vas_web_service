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


def test_delivery_records_what_the_device_now_holds(
    registered: TestClient, requests_mock, db_session
):
    requests_mock.get(PRODUCER_URL, content=b"PK\x03\x04")
    registered.get(DELIVERY_URL, headers=_auth())
    db_session.expire_all()
    registration = db_session.get(Registration, (DEVICE, PTID, SERIAL))
    record = db_session.get(PassRecord, (PTID, SERIAL))
    assert registration.delivered_tag == record.last_update_tag
    assert registration.last_delivered_at is not None


def test_a_wrong_token_answers_401_without_asking_the_producer(
    registered: TestClient, requests_mock
):
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
