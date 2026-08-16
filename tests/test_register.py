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


def test_a_repeated_registration_bumps_the_devices_updated_at(engine):
    """The push-token change has to be visible in `updated_at`, not only in the token.

    Two real transactions, on the `engine` fixture rather than through
    `client`/`db_session`, and that is forced by what `now()` means:
    PostgreSQL's `now()` is the *transaction's* start time, constant for its
    whole duration. `db_session` runs an entire test inside one outer
    transaction, so two upserts through it would read back the same timestamp
    however correct the code is -- the test would pass against the defect it is
    written to catch. In production each request is its own transaction, which
    is what these two `Session` blocks reproduce.

    A device of its own, cleaned up here: the `engine` fixture is module-scoped
    and these two commits are real, so a leftover row would be visible to every
    other test in this module.
    """
    from sqlmodel import Session

    from edutap.wallet_apple_vas_web_service.service import _upsert_device

    device_id = "updated-at-device"
    try:
        with Session(engine) as session:
            _upsert_device(session, device_id, "the-first-token")
            session.commit()
        with Session(engine) as session:
            _upsert_device(session, device_id, "the-second-token")
            session.commit()

        with Session(engine) as session:
            device = session.get(Device, device_id)
            assert device.push_token == "the-second-token"
            assert device.updated_at > device.created_at
    finally:
        with Session(engine) as session:
            leftover = session.get(Device, device_id)
            if leftover is not None:
                session.delete(leftover)
                session.commit()


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


def test_a_registration_that_already_exists_answers_200_without_erroring(
    client: TestClient, db_session
):
    """The registration row exists for a reason other than a prior POST through
    this endpoint -- a concurrent request that beat this one to the insert, for
    instance. `ON CONFLICT DO NOTHING` must answer 200 here, not raise the
    unhandled `IntegrityError` a plain `INSERT` would.
    """
    db_session.add(Device(device_library_identifier=DEVICE, push_token="a-push-token"))
    db_session.add(PassRecord(pass_type_identifier=PTID, serial_number=SERIAL, last_update_tag=1))
    db_session.add(
        Registration(
            device_library_identifier=DEVICE,
            pass_type_identifier=PTID,
            serial_number=SERIAL,
        )
    )
    db_session.commit()

    response = client.post(_url(), headers=_auth(), json={"pushToken": "a-push-token"})

    assert response.status_code == 200


def test_a_conflicting_pass_record_keeps_its_existing_update_tag(client: TestClient, db_session):
    """`ON CONFLICT DO NOTHING` on the pass record must not touch
    `last_update_tag`: it is the announcement state, and a losing insert
    overwriting it would un-announce a change already delivered.
    """
    db_session.add(Device(device_library_identifier=DEVICE, push_token="a-push-token"))
    db_session.add(PassRecord(pass_type_identifier=PTID, serial_number=SERIAL, last_update_tag=42))
    db_session.commit()

    client.post(_url(), headers=_auth(), json={"pushToken": "a-push-token"})

    assert db_session.get(PassRecord, (PTID, SERIAL)).last_update_tag == 42
