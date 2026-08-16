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


def test_a_refetch_of_an_unchanged_pass_still_records_the_contact(
    registered: TestClient, requests_mock, db_session
):
    """`last_delivered_at` moves on every successful delivery, not only on a new tag.

    Both of its purposes are about when the device last actually came --
    evidence that a voided pass was collected, and "pushed but never fetched"
    in support. A re-fetch of an unchanged pass is contact. Gated on the tag
    having moved, the column would mean "first delivery of the current tag"
    while being called `last_delivered_at`, and the second fetch here would
    leave it untouched.
    """
    requests_mock.get(PRODUCER_URL, content=b"PK\x03\x04")

    registered.get(DELIVERY_URL, headers=_auth())
    db_session.expire_all()
    first = db_session.get(Registration, (DEVICE, PTID, SERIAL)).last_delivered_at

    # Nothing changes the pass in between: the tag is the same on both fetches.
    registered.get(DELIVERY_URL, headers=_auth())
    db_session.expire_all()
    registration = db_session.get(Registration, (DEVICE, PTID, SERIAL))

    assert registration.last_delivered_at > first


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
    # has permanently withdrawn cannot be reported as 410 without leaving the
    # contract. 401 is the strongest thing those two codes let us say.
    requests_mock.get(PRODUCER_URL, status_code=410)
    assert registered.get(DELIVERY_URL, headers=_auth()).status_code == 401


def test_a_pass_the_producer_does_not_have_answers_503(registered: TestClient, requests_mock):
    """`404` is recoverable and must not be reported as a dead credential.

    A producer mid-deploy, a restored replica or a mistyped template all show
    up as `404`. A `401` tells the device its authentication token is no good,
    which Wallet does not recover from -- it does not re-authenticate. A `503`
    is a "come back later" the device already handles. The two used to be
    collapsed into one answer.
    """
    requests_mock.get(PRODUCER_URL, status_code=404)
    assert registered.get(DELIVERY_URL, headers=_auth()).status_code == 503


def test_delivery_to_an_unregistered_device_still_works(client: TestClient, requests_mock):
    # A device may fetch a pass it never registered; Apple's flow does not
    # require registration before delivery. Nothing is recorded in that case.
    requests_mock.get(PRODUCER_URL, content=b"PK\x03\x04")
    assert client.get(DELIVERY_URL, headers=_auth()).status_code == 200
