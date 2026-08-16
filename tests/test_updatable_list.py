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
    assert (
        registered.get(LIST_URL, params={"passesUpdatedSince": "not-a-number"}).status_code == 200
    )


def test_an_unknown_device_gets_204(client: TestClient):
    unknown = "/apple_update_service/v1/devices/no-such-device/registrations/" + PTID
    assert client.get(unknown).status_code == 204
