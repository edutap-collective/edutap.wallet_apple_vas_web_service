"""The Apple Wallet web service endpoints: register, list, unregister, deliver, log."""

import logging
from contextlib import asynccontextmanager
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import Response
from sqlmodel import Session

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

    device = session.get(Device, deviceLibraryIdentifier)
    if device is None:
        session.add(
            Device(device_library_identifier=deviceLibraryIdentifier, push_token=data.pushToken)
        )
    elif device.push_token != data.pushToken:
        # A device that re-registers with a new token has moved; pushing to the
        # old one would silently reach nobody.
        device.push_token = data.pushToken
        session.add(device)

    if session.get(PassRecord, (passTypeIdentifier, serialNumber)) is None:
        session.add(
            PassRecord(
                pass_type_identifier=passTypeIdentifier,
                serial_number=serialNumber,
                last_update_tag=next_update_tag(session),
            )
        )

    existing = session.get(
        Registration, (deviceLibraryIdentifier, passTypeIdentifier, serialNumber)
    )
    if existing is not None:
        session.commit()
        return Response(status_code=200)

    session.add(
        Registration(
            device_library_identifier=deviceLibraryIdentifier,
            pass_type_identifier=passTypeIdentifier,
            serial_number=serialNumber,
        )
    )
    session.commit()
    return Response(status_code=201)


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
