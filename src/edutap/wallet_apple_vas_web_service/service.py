"""The Apple Wallet web service endpoints: register, list, unregister, deliver, log."""

import logging
from contextlib import asynccontextmanager
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import Response
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, select

from .config import AppleWalletWebServiceSettings, get_settings
from .db_models import UPDATE_TAG_SEQUENCE, Device, PassRecord, Registration
from .http_models import AppleWalletWebServiceAuthorizationPayload, LogEntries
from .session import get_session
from .tokens import verify_authorization

LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(router: APIRouter):
    """Router lifespan; nothing to set up or tear down yet."""
    # setup phase
    yield
    # shutdown


router = APIRouter(
    prefix="/apple_update_service/v1",
    lifespan=lifespan,
)


"""
see: https://developer.apple.com/documentation/walletpasses/adding_a_web_service_to_update_passes
"""


def next_update_tag(session: Session) -> int:
    """Return the next value of the update-tag sequence.

    Through the `Sequence` construct rather than a hand-written `nextval(...)`:
    the sequence lives in this package's schema, and a bare name would resolve
    through `search_path` to whatever the connecting role happens to look at.
    """
    return session.execute(sa.select(UPDATE_TAG_SEQUENCE.next_value())).scalar_one()


def _authorized(
    authorization: str | None,
    settings: AppleWalletWebServiceSettings,
    pass_type_identifier: str,
    serial_number: str,
) -> bool:
    """Whether this request carries the authentication token of this pass."""
    if not settings.auth_required:
        return True
    return verify_authorization(
        authorization, settings.accepted_secrets(), pass_type_identifier, serial_number
    )


def _upsert_device(session: Session, device_library_identifier: str, push_token: str) -> None:
    """Insert the device, or refresh its push token if it already exists.

    `ON CONFLICT DO UPDATE`, not a `SELECT` followed by an `INSERT`/`UPDATE`: a
    phone and its paired watch are two different devices that register the same
    pass within milliseconds of each other -- an ordinary case, not an edge
    one. A check-then-act would let the loser of that race hit an unhandled
    `IntegrityError` on `commit()` and turn into a bare 500 instead of taking
    the row it should have updated.
    """
    statement = (
        pg_insert(Device.__table__)
        .values(device_library_identifier=device_library_identifier, push_token=push_token)
        .on_conflict_do_update(
            index_elements=["device_library_identifier"],
            set_={"push_token": push_token},
        )
    )
    session.execute(statement)


def _upsert_pass_record(session: Session, pass_type_identifier: str, serial_number: str) -> None:
    """Insert the pass record if it does not exist yet; never touch an existing one.

    `DO NOTHING`, not `DO UPDATE`: `last_update_tag` is the announcement state,
    and a racing insert overwriting it would un-announce a change already
    delivered. The sequence value this call consumes is spent either way --
    tags only have to rise, not be dense, so one going unused because this
    insert lost the race is harmless.
    """
    statement = (
        pg_insert(PassRecord.__table__)
        .values(
            pass_type_identifier=pass_type_identifier,
            serial_number=serial_number,
            last_update_tag=next_update_tag(session),
        )
        .on_conflict_do_nothing(index_elements=["pass_type_identifier", "serial_number"])
    )
    session.execute(statement)


def _insert_registration_if_absent(
    session: Session,
    device_library_identifier: str,
    pass_type_identifier: str,
    serial_number: str,
) -> bool:
    """Insert the registration if absent; return whether this call created it.

    `RETURNING` on a `DO NOTHING` insert answers "did I just create this row?"
    in the same round trip that does the inserting -- no separate `SELECT`
    whose answer a concurrent insert could invalidate before this statement
    runs. That answer is exactly Apple's 201-vs-200 distinction.
    """
    statement = (
        pg_insert(Registration.__table__)
        .values(
            device_library_identifier=device_library_identifier,
            pass_type_identifier=pass_type_identifier,
            serial_number=serial_number,
        )
        .on_conflict_do_nothing(
            index_elements=[
                "device_library_identifier",
                "pass_type_identifier",
                "serial_number",
            ]
        )
        .returning(Registration.__table__.c.device_library_identifier)
    )
    return session.execute(statement).first() is not None


@router.post("/devices/{deviceLibraryIdentifier}/registrations/{passTypeIdentifier}/{serialNumber}")
async def register_pass(
    deviceLibraryIdentifier: str,
    passTypeIdentifier: str,
    serialNumber: str,
    authorization: Annotated[str | None, Header()] = None,
    data: AppleWalletWebServiceAuthorizationPayload | None = None,
    *,
    settings: AppleWalletWebServiceSettings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> Response:
    """Register a device to receive update notifications for a pass.

    https://developer.apple.com/documentation/walletpasses/register-a-pass-for-update-notifications

    Apple's tasks for this call: create the pass entry if it does not exist,
    create the device entry if it does not exist, store the mapping between
    them. Answers 201 on a new registration, 200 when the serial number was
    already registered for the device, 401 when unauthorized.
    """
    if not _authorized(authorization, settings, passTypeIdentifier, serialNumber):
        return Response(status_code=401)

    # A missing body is a bad request, not an assertion: `assert` disappears
    # under `python -O`, and a None would reach `data.pushToken` below and turn
    # a malformed registration into a 500.
    if data is None:
        return Response(status_code=400)

    _upsert_device(session, deviceLibraryIdentifier, data.pushToken)
    _upsert_pass_record(session, passTypeIdentifier, serialNumber)
    created = _insert_registration_if_absent(
        session, deviceLibraryIdentifier, passTypeIdentifier, serialNumber
    )
    session.commit()
    return Response(status_code=201 if created else 200)


@router.delete(
    "/devices/{deviceLibraryIdentifier}/registrations/{passTypeIdentifier}/{serialNumber}"
)
async def unregister_pass(
    deviceLibraryIdentifier: str,
    passTypeIdentifier: str,
    serialNumber: str,
    authorization: Annotated[str | None, Header()] = None,
    *,
    settings: AppleWalletWebServiceSettings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> Response:
    """Stop sending update notifications for a pass on a device.

    https://developer.apple.com/documentation/walletpasses/unregister-a-pass-for-update-notifications

    Apple's tasks: delete the mapping, then delete the device if no registration
    is left for it. Answers 200 or 401 — the two codes Apple documents.
    """
    if not _authorized(authorization, settings, passTypeIdentifier, serialNumber):
        return Response(status_code=401)

    registration = session.get(
        Registration, (deviceLibraryIdentifier, passTypeIdentifier, serialNumber)
    )
    if registration is not None:
        session.delete(registration)
        session.flush()

    # Lock the device row before deciding whether it still holds any
    # registrations: that decision is a negative existence check ("nothing
    # references this device"), and Postgres has no lock that protects the
    # *absence* of a row -- only rows that exist can be locked. The
    # registration's foreign key does not close this gap either: a concurrent
    # `_upsert_device` only needs a `KEY SHARE` lock to satisfy that
    # constraint, which does not conflict with a plain read here, so this
    # transaction could still see "no registrations left" while a
    # registration for this device is being created elsewhere -- and then
    # delete the device out from under it. `with_for_update=True` takes the
    # stronger lock `_upsert_device`'s `ON CONFLICT DO UPDATE` also needs,
    # so a concurrent registration for this device blocks here until this
    # transaction commits or rolls back: whichever side finishes first is
    # the one the other one sees. Do not remove this thinking it is
    # redundant with the foreign key -- it is not.
    device = session.get(Device, deviceLibraryIdentifier, with_for_update=True)

    remaining = session.exec(
        select(Registration).where(
            Registration.device_library_identifier == deviceLibraryIdentifier
        )
    ).first()
    if remaining is None and device is not None:
        session.delete(device)

    session.commit()
    return Response(status_code=200)


@router.post("/log")
async def device_log(
    request: Request,
    data: LogEntries,
):
    """Logging/Debugging from the device

    log an error or unexpected server behavior, to help with server debugging
    POST /v1/log
    JSON payload: { "description" : <human-readable description of error> }

    server response: 200
    """
    # To the process log, not to a file: three replicas write to three container
    # filesystems that nothing collects and the next deploy discards. The path this
    # wrote to does not exist in the image either. Structured logging with the rest
    # of the estate's fields comes with edutap.observability_settings.
    for line in data.logs:
        LOGGER.info("apple device log: %s", line)

    return Response(status_code=200)
