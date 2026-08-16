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


def test_a_cursor_below_our_own_record_still_lists_the_pass(registered: TestClient, db_session):
    # delivered_tag is caught up, so arm 1 of the filter no longer decides --
    # this is the one configuration where the cursor arm is the one that must
    # list the pass, because the device's own claim says it is behind.
    registration = db_session.get(Registration, (DEVICE, PTID, SERIAL))
    record = db_session.get(PassRecord, (PTID, SERIAL))
    registration.delivered_tag = record.last_update_tag
    db_session.add(registration)
    db_session.commit()
    body = registered.get(
        LIST_URL, params={"passesUpdatedSince": str(record.last_update_tag - 1)}
    ).json()
    assert body["serialNumbers"] == [SERIAL]


def test_a_cursor_at_or_above_our_own_record_yields_204(registered: TestClient, db_session):
    # The mirror case: with delivered_tag caught up and a cursor that is not
    # behind either, nothing should be listed.
    registration = db_session.get(Registration, (DEVICE, PTID, SERIAL))
    record = db_session.get(PassRecord, (PTID, SERIAL))
    registration.delivered_tag = record.last_update_tag
    db_session.add(registration)
    db_session.commit()
    response = registered.get(LIST_URL, params={"passesUpdatedSince": str(record.last_update_tag)})
    assert response.status_code == 204


@pytest.mark.parametrize("overflowing_cursor", ["inf", "1e400"])
def test_an_overflowing_cursor_is_answered_as_if_absent(
    registered: TestClient, db_session, overflowing_cursor: str
):
    # float("inf") and float("1e400") both parse without raising -- they
    # reach the OverflowError only at int(), and from different sources (a
    # literal infinity token vs. decimal magnitude overflow), so one shape
    # passing does not prove the other does. This route has no credential --
    # any caller can send this -- so it must be answered, and answered as if
    # no cursor had been sent: with delivered_tag caught up and no other
    # cursor given, that is the 204 test_a_current_device_gets_204 already
    # establishes.
    registration = db_session.get(Registration, (DEVICE, PTID, SERIAL))
    record = db_session.get(PassRecord, (PTID, SERIAL))
    registration.delivered_tag = record.last_update_tag
    db_session.add(registration)
    db_session.commit()
    response = registered.get(LIST_URL, params={"passesUpdatedSince": overflowing_cursor})
    assert response.status_code == 204
